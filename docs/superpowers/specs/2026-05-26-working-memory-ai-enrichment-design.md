# Working Memory AI Enrichment — Design Spec

**Date:** 2026-05-26
**Branch:** `wm/ai-enrichment` (off `dev-juan`) — proposed
**Status:** Design approved (brainstorming) — ready for `/writing-plans`
**Replaces** the regex extraction in `WorkingMemoryReducer` (`beacon.command_output` path) with LLM-based enrichment. The **GATE** (Layer A Sigma + Layer B judge), the event bus, the verdict pipeline, and the `.cna` are **untouched**.

---

## 1. Context & motivation

`WorkingMemoryReducer` populates the Working Memory (L2) by **regex-parsing raw beacon command output**. The regexes are English-locale-locked and format-fragile, so they lose information silently:

- **#149** — `systeminfo` lands only `Host Name` (`_HOSTNAME_LINE`); it drops OS, OS version, **Domain**, and **NIC IPs**. Confirmed live: `wm.json` host had `name` only.
- **#145** — the kerberos finding regex (`_KERBEROS_TICKET`) matches `Cached Tickets` unconditionally, so `klist` output containing `Cached Tickets: (0)` produces a **false-positive** finding.

These are not isolated bugs — they are symptoms of deterministic regex over arbitrary operational text. Any format change, locale change, or novel tool silently drops facts. A red-team operation surfaces **all kinds** of information (AD, cloud, web, files), not just the handful of shapes the regexes know.

**Why this matters now:** the deferred **AI Advisor** will base next-step recommendations on the 5-layer memory system. If the Working Memory is lossy, the Advisor reasons on bad data. The WM must be **accurate**. Of the 5 layers, **only the WM has this problem** — it is the only layer that parses raw output. L1 (long-term) and L3 (episodic) ingest already-structured data; L5 (context summary) is already LLM-powered; L4 (tradecraft) is deferred to v0.2; the KB is embedding-based. So enrichment is scoped to the WM.

### The boundary: GATE stays deterministic, ENRICHMENT becomes LLM

The **gate** (Layer A Sigma rules → synchronous BLOCK/WARN/ALLOW in <150ms; Layer B LLM judge async for novel patterns) is the safety decision and **stays deterministic, fast, and offline-capable**. WM **enrichment** is situational-awareness / display — it runs *after* a command executes, off the critical path — so it is free to be LLM-based. This spec changes only enrichment.

---

## 2. Scope

### In scope (build now)
- Replace the `beacon.command_output` regex extraction in `WorkingMemoryReducer` with an async **LLM enrichment** path (new `WorkingMemoryEnricher`).
- LLM emits **validated JSON deltas**; the reducer (single WM writer) merges them deterministically via a new `apply_deltas`.
- **Schema extensions** required to fix #149/#145 (Host gains domain/IPs/OS-version; Credential generalized beyond NTLM).
- **Map-reduce** handling for outputs that exceed a comfortable single-call size (chunk + merge).
- **Observability** (3 tiers) and **cost controls** as first-class requirements.
- **Delete** the regex extraction and the helpers it orphans — **no dead code**.

### Deferred (future, separate plans)
- **Operational Journal + memory-system for the Assistant** — a 6th memory layer holding the raw `(command, output, metadata)` transcript, with RAG retrieval, so the Assistant is aware of the *whole* operation. The current Enricher already receives `(command, output)` for every non-trivial command → it is the natural future write-point, so this design does not paint us into a corner. **Its own brainstorm + spec.** (See §13.)
- **AI Advisor logic** — consumes the now-accurate memory; already deferred.
- **TUI secret masking** — store the secret value now (§5); mask in display (`••••`, reveal on demand) later.
- **Per-engagement rate-cap** — optional cost guardrail (§10).
- **Local models (Ollama)** — documented (§12) as a zero-architecture-change port swap; not built here.

### Out of scope / untouched
- The gate (Layer A/B), the event bus, the verdict pipeline, the `.cna`, the other 4 memory layers. The `beacon.initial → Session` and `opsec.verdict → Finding` paths in the reducer are **unchanged** (they consume structured events, not raw text).

---

## 3. Locked decisions (from brainstorm)

| Axis | Decision |
|---|---|
| **Trust boundary** | Full cloud (Anthropic via the existing `LLMProvider`), **full output** sent, opt-in per engagement (same consent as Layer B). Local (Ollama) = documented v0.2 future. |
| **LLM port** | Reuse `domain/llm.py::LLMProvider.complete_with_cache` — no new SDK, no Google ADK. |
| **Trigger** | **Enrich-by-default + skip a tuneable denylist** of trivial commands + a min-output-length floor. NOT an allowlist (an allowlist re-introduces the fragility — a novel tool not listed would be silently skipped). The denylist **fails toward enrichment**. |
| **Architecture** | LLM **extracts** → emits validated JSON deltas → **reducer is the single WM writer** and merges deterministically. The Enricher never writes the WM. |
| **Large outputs** | **Map-reduce** (`stuff` if it fits / chunk+merge if not), 10–20% overlap, **context per chunk**, parallel map. **NOT RAG** (RAG is lossy → wrong for exhaustive extraction; RAG belongs to the future Assistant over the Journal). |
| **Model** | Haiku-class + prompt caching (the `complete_with_cache` 5-block cache layout). |
| **Errors** | Best-effort: never crash the daemon, never block the gate. **1 retry** on malformed JSON, then log + drop. |
| **Observability** | 3 tiers: structured logs / bus events → TUI / `doctor` health probe. |
| **Cost** | denylist + Haiku + caching + chunking + token-tracking + async + optional rate-cap. |
| **No dead code** | Delete the command-output regex + the now-orphaned `upsert_host`/`upsert_credential`; absorb their merge logic into `apply_deltas`. |

---

## 4. Architecture

### Boundary
The gate is untouched. The change is isolated to the WM population path. Today the regex lives **inside** `WorkingMemoryReducer._on_beacon_command_output`; it moves **out** into a new async component. The reducer keeps its deterministic structured-event handling (`beacon.initial → Session`, `opsec.verdict → Finding`) and gains a deltas-merge handler.

### Components (each one responsibility)
1. **`EnrichmentTrigger`** *(pure function + config)* — `(command, output) → enrich?`. Denylist of trivial commands + min-length floor. Config-driven (YAML), operator-tuneable.
2. **`WorkingMemoryEnricher`** *(async)* — subscribes to `beacon.command_output`. If the trigger says enrich: chunk if needed (§7), build the extraction prompt per chunk (`command + beacon_id` context + chunk text), call `LLMProvider`, collect JSON. **Does not write the WM.** Emits an internal `working_memory.deltas` event. Best-effort: any failure → log + emit a failure event + drop (§8/§9).
3. **`DeltaParser` + the contract** *(§5)* — parse the LLM JSON, validate against the schema, **whitelist known fields only** (unknown keys ignored → anti-hallucination + crash-safe), construct typed domain objects, attach system-supplied provenance (`source_beacon_id`).
4. **`apply_deltas` (in the reducer / repo, single WM writer)** *(§6)* — consume the deltas event and merge deterministically (dedup by natural key, append-only findings, provenance), in **one** `load → apply batch → save` transaction.

### Single-writer (the key invariant)
`wm.json` is a shared mutable resource written by several paths (`beacon.initial`, `opsec.verdict`, and now enrichment). Two concurrent read-modify-write cycles cause **lost updates** (one save clobbers the other) — the atomic save fixed in PR #7 prevents *corruption* (a half-written file) but **not** lost updates. Fix: **only the reducer writes the WM.** The Enricher produces deltas and emits them as an event; the reducer applies them serially alongside the other event types (no lock needed — the reducer already processes events one at a time). The async LLM call runs off to the side; only the cheap merge is serialized.

### Event flow
```
beacon.command_output ──> EnrichmentTrigger ──skip──> (drop, optional debug log)
                              │ enrich
                              ▼
                        WorkingMemoryEnricher ──(chunk if needed; per-chunk: LLMProvider, Haiku, cached system)──> JSON
                              │ parse + validate + whitelist + provenance
                              ▼ emits working_memory.deltas (internal event)
                        WorkingMemoryReducer  ← SINGLE WM writer
                              │ apply_deltas: merge (dedup + provenance), one load→save
                              ▼
                        WorkingMemoryRepo.save (atomic) ──> re-emit working_memory_snapshot (#146) ──> TUI live
```
Re-emit of the snapshot on mutation already exists (#146), so the TUI updates live with no new plumbing.

---

## 5. The delta contract + schema changes

### The JSON the LLM emits (strict; any array may be empty)
```json
{
  "hosts":       [{"name": "...", "os": "...", "os_version": "...", "domain": "...", "ip_addresses": ["..."]}],
  "credentials": [{"domain": "...", "username": "...", "secret_type": "...", "secret": "..."}],
  "findings":    [{"kind": "...", "summary": "..."}],
  "tools_used":  [{"name": "...", "used_on": "...", "detection_observed": "..."}],
  "defenses_observed": ["..."]
}
```
- The five arrays are exactly the WM fields derived from **output parsing**. Excluded by design: `sessions` (from `beacon.initial`), `scope`/`client`/`rules_of_engagement` (from `engagement.yaml`, #156), and `objectives`/`current_phase`/`kill_chain_status` (**interpretation** = the Advisor's job, not extraction).
- **Provenance is system-supplied, not LLM-supplied.** The Enricher attaches `source_beacon_id` (known from the event) when constructing the typed objects — the LLM cannot fabricate provenance.

### Grounding rules (in the cached system prompt)
- Extract **only** facts literally present in the output. Do **not** infer, guess, or invent.
- If a field is absent, omit it or set null. Never invent a value.
- Understand semantics: `Cached Tickets: (0)` means **zero** tickets — do **not** report a ticket finding. *(fixes #145)*
- Return **only** valid JSON matching the schema. No prose.

### Schema changes (`domain/memory.py`)
**`Host`** gains three fields (#149 cannot be fixed without them — there is nowhere to put domain/IPs today):
```python
domain: str | None = None
ip_addresses: list[str] = field(default_factory=list)
os_version: str | None = None
```
**`Credential`** is generalized beyond NTLM (mimikatz yields plaintext passwords, AES keys, and Kerberos tickets — NTLM-only silently drops the prize):
```python
secret_type: str = "ntlm"   # OPEN vocabulary: ntlm | plaintext | aes256 | dcc2 | kerberos_ticket | aws_key | azure_token | ...
secret: str | None = None    # the captured material (value stored — see decision below)
ntlm_hash: str | None = None # retained for back-compat with existing wm.json
```
- `secret_type` is an **open `str`**, not an `Enum`, so non-AD material (cloud tokens, etc.) needs no schema change. The `Finding` type (`kind` + `summary`) remains the generic catch-all for anything unstructured. The contract is **not** AD-specific.
- **Store the secret value** (decision): `wm.json` is the operator's loot file under `~/.redrobin/` (zero teamserver footprint), consistent with the full-output decision; storing only the fact would re-introduce loss. Caveat: the value also rides the `working_memory_snapshot` and renders in the TUI → **TUI masking is future polish** (§2).

### Back-compat (verified)
`WorkingMemoryRepo._from_dict` builds nested objects with `Host(**h)` / `Credential(**c)` over `data.get(..., [])`. New fields are **additive with defaults** → an old `wm.json` lacking them loads cleanly (the default fills the missing kwarg). **No migration script.** Note: the parser must construct typed objects from **whitelisted** keys so a stray key never reaches `Host(**…)` (which would raise) — see §3 DeltaParser.

---

## 6. Merge semantics + dead-code removal

### `apply_deltas` (single `load → apply batch → save`)
Today each `upsert_*` helper does its own `load → mutate → save` (N file rewrites per output). The deltas merge applies the **whole batch in one transaction** (more efficient, one consistent snapshot, one re-emit). Rules (reuse the existing `upsert_host` non-null-overwrite semantics):

| Type | Key | Policy |
|---|---|---|
| host | `name` (upper) | non-null overwrite on scalars (`os`, `os_version`, `domain`, ...); **set-union** on `ip_addresses` (accumulate NICs across observations) |
| credential | `(domain, username, secret_type)` | dedup / replace |
| finding | — | **append** (audit stream — existing contract) |
| defenses_observed | value | set-union |
| tools_used | `(name, used_on)` | dedup |

### No dead code (grep-verified callers)
| Helper | Caller(s) today | After deleting the command-output regex |
|---|---|---|
| `upsert_host` | **only** `reducer._on_beacon_command_output:165` | orphaned → **logic absorbed into `apply_deltas`, method deleted** |
| `upsert_credential` | **only** `reducer._on_beacon_command_output:160` | orphaned → **absorbed + deleted** |
| `append_finding` | command_output:173 **+** `opsec.verdict:225` | **kept** (verdict path still uses it) |
| `upsert_session` | `beacon.initial:190` | **kept** (structured path) |

**Delete:** `WorkingMemoryReducer._on_beacon_command_output` body + regexes `_NTLM_LINE` / `_HOSTNAME_LINE` / `_KERBEROS_TICKET` + `WorkingMemoryRepo.upsert_host` / `upsert_credential`.
**Add:** `WorkingMemoryRepo.apply_deltas(...)` + `WorkingMemoryReducer._on_enrichment_deltas(...)`.

---

## 7. Large outputs: map-reduce (not RAG)

Most outputs fit in a single comfortable call (`systeminfo`, `klist`, `whoami /all`, `mimikatz`, `net group`) → **one call, no chunking**. Only genuinely large outputs (e.g. a recursive share listing) are chunked.

**Why map-reduce and not RAG:** RAG retrieves the top-k chunks relevant to a *query* and **drops the rest** — lossy by design, which violates the prime directive (don't lose info). Our task is **exhaustive extraction** (every fact, no query). Map-reduce processes **every** chunk → nothing dropped. It is also *simpler* than RAG here (no embedding/vector-store/retrieval — just split, extract-each, merge; the merge already exists). RAG is the right tool for the **future Assistant** querying the operational history (§13), not for extraction.

**Vendor-validated pattern** (this is `stuff`/`map_reduce`, the canonical documented chains):
- OpenAI Cookbook — *Summarizing Long Documents* (chunk → piecewise → combine; map-reduce + recursive).
- Anthropic docs — *Legal Summarization* meta-summarization (chunk ~20k chars, process each, combine). Two tips folded in: **append document-level context to each chunk** (boosts accuracy ~50-60% → 72-75%) and **parallelize the calls**.
- LangChain — first-class `stuff` / `map_reduce` / `refine` chains; map_reduce "mirrors the classic Map-Reduce pattern from distributed computing — scalable, parallelizable, cost-efficient". (We use map-reduce, not `refine`: extraction has no sequential dependency, and our dedup-merge is a natural Reduce.)

**Algorithm:**
```python
def chunk(output: str, max_tokens=30_000, overlap=0.15) -> list[str]:
    if count_tokens(output) <= max_tokens:
        return [output]                       # 99% case — single chunk
    lines = output.splitlines()               # always split on a line boundary
    chunks, start = [], 0
    while start < len(lines):
        end = advance_until_token_budget(lines, start, max_tokens)
        chunks.append("\n".join(lines[start:end]))
        if end >= len(lines):
            break
        start = end - int((end - start) * overlap)   # step back 15% = the overlap
    return chunks
```
- **Line-boundary split** (never mid-line/mid-record) + **10–20% overlap** (a record straddling a boundary appears intact in the next chunk).
- **Context per chunk:** every chunk's extraction call carries `command + beacon_id` (Anthropic's accuracy tip), not just the raw chunk.
- **Parallel map:** chunks extracted concurrently (async); each shares the cached system prompt → cache-hit per chunk.
- **Reduce = `apply_deltas`:** all chunks' deltas merged; the natural-key **dedup collapses overlap duplicates** (a record in the shared zone extracted twice → one row). `max_tokens` sits well below the hard context limit to dodge the "lost in the middle" degradation.

---

## 8. Error handling & degradation

Enrichment is **best-effort** and never crashes the daemon or blocks the gate.
- **LLM failure** (timeout / network / rate-limit / API error) → catch, log `ERROR`, emit `enrichment.failed`, drop. WM unchanged for that output. *(Mirrors `L5Summarizer`'s `except Exception: logger.exception(...)`.)*
- **Malformed / invalid JSON** → **one retry** with a corrective nudge ("your previous response was not valid JSON matching the schema; return ONLY valid JSON"). Second failure → log + failure event + drop. Bounded to 1 retry (no loop, no runaway cost). Each retry is counted + logged.
- **Hallucinated fields** → the DeltaParser constructs typed objects from **whitelisted** keys only; unknown keys are ignored (no crash, no pollution).
- **Degraded mode** (no `ANTHROPIC_API_KEY`, `REDROBIN_ALLOW_DEGRADED=1`) → the Enricher has no LLM → enrichment **disabled cleanly** (as the summarizer does when `llm is None`). The WM still works via the deterministic `beacon.initial` / `opsec.verdict` paths.

---

## 9. Observability (first-class)

Enrichment is async and silent — a failure must be **visible**, not buried. Three tiers, all reusing existing infrastructure:

1. **Structured logs** (forensic) — `logging.getLogger(__name__)` (daemon-wide pattern). `INFO` on success (`command`, `n_deltas`, `latency_ms`, tokens), `WARNING` on retry/degraded/truncation, `ERROR` on failure (reason + exception).
2. **Bus events → TUI (live)** — `tui_ws` forwards **every** bus event to the TUI. The Enricher publishes `enrichment.completed` (`{command, n_deltas, latency_ms, tokens}`) and `enrichment.failed` (`{command, beacon_id, reason, retry_count}`) → they render in the **TUI Events panel** in real time. **Zero new plumbing.** This is what makes silent async failure loud.
3. **`doctor` health probe (aggregate)** — extend `doctor_probes.py` with cumulative counters (attempts / ok / failed / retried / dropped / truncated + total tokens) → `redrobin doctor` shows e.g. *"Enrichment: 142 ok / 3 failed / 1 retried, ~Xk tokens"*.

---

## 10. Cost

Engineered, measured, and bounded against the worst case — not "Haiku is cheap, trust me":
- **Denylist** → zero calls on trivial commands.
- **Haiku-class model** — extraction is pattern-recognition + JSON formatting, not reasoning; a frontier model is overkill and ~10–20× the price (same logic the summarizer already follows).
- **Prompt caching** — the static system block (instructions + schema + grounding + few-shot, ~1–2k tokens, byte-identical every call) is cached (~10% on cache-read). Recurring cost ≈ the dynamic output + small JSON out. Over an engagement, the static prompt amortizes to ~free (~10× reduction on that portion).
- **Chunking, not capping** — bounds per-call tokens without losing information; cost scales linearly (and honestly) with output size; every chunk is a cache-hit on the system block.
- **Token tracking** — `LLMResponse` returns `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens`; logged + aggregated (§9 tier 3) → spend is measured, not guessed.
- **Async** → no operator latency (cost ≠ latency).
- **Optional rate-cap** (future) — a per-engagement calls/min ceiling guards against a script flooding outputs.

---

## 11. Testing

- **Unit** — `EnrichmentTrigger` (denylist + length floor, pure fn); `DeltaParser` (valid → typed; malformed → drop; extra fields → ignored; the grounding cases); `apply_deltas` (host dedup + non-null overwrite + IP set-union; cred dedup; finding append; defense union; tool dedup); `chunk` (single chunk when it fits; multi-chunk + overlap + line boundaries).
- **Integration** — a **stub `LLMProvider`** returning canned deltas → drive a `beacon.command_output` event → assert WM updated + `working_memory_snapshot` re-emitted (reuse the summarizer's stub pattern; **no real API calls in CI**).
- **Golden fixtures** — real `systeminfo` / `klist` / `mimikatz` output dumps with a stubbed LLM returning the expected deltas; assert the merge (#149 fields present; #145 no FP; plaintext credential captured). The fixtures document the contract.
- **Optional real-LLM** — a few fixtures against the real Anthropic API, gated behind a key + a marker, **off in CI** (costs money); validates the prompt actually extracts.
- **Live smoke** (the real acceptance) — real beacon → `systeminfo` / `klist` / `mimikatz` → open the Inspector → hosts/creds complete, no kerberos FP; observe `enrichment.*` events in the TUI Events panel.

---

## 12. Local models (v0.2, documented — not built)

The Enricher depends only on the `LLMProvider` port, so v0.2 swaps an Ollama-backed implementation with **zero architecture change**. Tradeoffs to document: local models are weaker at **strict JSON** (may need grammar-constrained decoding + more validation/retries) and may **miss fields** a frontier model catches — **but zero egress** (output never leaves the box = the OpSec endgame). v0.1 cloud = the quality/simplicity baseline; v0.2 local = the OpSec goal.

---

## 13. Out of scope / future work

**Operational Journal + memory-system for the Assistant** (its own brainstorm + spec). The WM is a *distilled* facts store; it deliberately discards raw output after extraction. For the Assistant to be aware of the **whole** operation, a 6th layer is needed: an append-only **Operational Journal** of `(command, output, metadata)` — no current layer holds the raw transcript (L3 has verdicts only, gated only; the WM discards raw output). The Assistant would consume context at scale without prompt-stuffing:

```
Assistant context = WM (current state)
                  + L5 summary (narrative gist, already exists)
                  + RAG over the Journal (Voyage embeddings — already in the stack)
                  + L1 lessons (cross-engagement)
```

This is where **RAG** correctly belongs (query-driven retrieval over a large corpus). The current Enricher already receives `(command, output)` for every non-trivial command → it is the natural future **write-point** for the Journal, so the present design is forward-compatible. The broader goal is to **leverage/evolve the existing 5-layer memory system into a real operational substrate for the AI Advisor.**

Also deferred: **AI Advisor logic**; **TUI secret masking**; **per-engagement rate-cap**.

---

## 14. Acceptance criteria

- **#149** — `shell systeminfo` → the host in the WM has `os`, `os_version`, `domain`, and `ip_addresses` populated (not just `name`).
- **#145** — `klist` output with `Cached Tickets: (0)` produces **no** kerberos finding; real cached tickets produce a finding with the actual detail.
- **Credentials** — `mimikatz sekurlsa::logonpasswords` captures plaintext passwords (and AES/tickets) via `secret_type`/`secret`, not only NTLM.
- **Best-effort** — with the LLM unreachable, the daemon does not crash, the gate is unaffected, the WM is simply not enriched for that output.
- **Large output** — a multi-chunk output is fully extracted (a fact in the last chunk lands in the WM); overlap does not produce duplicates.
- **Observability** — an induced failure appears as `enrichment.failed` in the TUI Events panel and increments the `doctor` failed counter.
- **No dead code** — the command-output regex and the orphaned `upsert_host`/`upsert_credential` are gone; `apply_deltas` is their single replacement.
- **Tests** green (unit + integration + golden, no real API in CI); **live smoke** green.

---

## 15. References

- Long Context vs. RAG for LLMs — arXiv 2501.01880
- LLM×MapReduce — arXiv 2410.09342
- OpenAI Cookbook — Summarizing Long Documents — https://cookbook.openai.com/examples/summarizing_long_documents
- Anthropic — Legal Summarization (chunking + meta-summarization, context-per-chunk, parallelize) — https://docs.anthropic.com/en/docs/about-claude/use-case-guides/legal-summarization
- LangChain — stuff / map_reduce / refine summarization chains
- Google Cloud — Parse and chunk documents — https://docs.cloud.google.com/generative-ai-app-builder/docs/parse-chunk-documents
