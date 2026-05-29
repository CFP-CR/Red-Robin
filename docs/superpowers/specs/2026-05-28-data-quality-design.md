# Data-Quality Phase — Design

> **Status:** approved (brainstorm 2026-05-28). Umbrella spec for ~4 separate
> implementation plans. Each group below becomes its own
> `/writing-plans → /auditing-plans → /subagent-driven-development → live smoke`
> cycle. Branch off `dev-juan` (B1+B1.5+B2+B3 merged, HEAD `49b1aba`).

## 1. Problem

The live `manual-smoke` runs surfaced five working-memory data-quality defects.
They share one consequence: **they poison the substrate the future AI assistant
reads.** The assistant is an interpretation layer over working memory — garbage
in the substrate becomes garbage advice. Clean data is therefore the locked
prerequisite before the assistant brainstorm.

The five tracked issues:

- **#105** — a username (`JCSPI`) is extracted as a host.
- **#114** — the logged-in user is extracted as a credential with no secret.
- **#106** — the Inspector ACCOUNTS section is always empty (dead `Account` model).
- **#107** — SESSIONS is empty after a working-memory clean despite an active beacon.
- **#103** — ROE / objectives / `current_phase` are never populated.

## 2. Guiding principle — the working-memory writer-split

Working memory has three classes of field, each with **exactly one writer**.
This contract is the line that separates *what data-quality fixes now* from
*what the future assistant does*.

| Concern | Writer | `WorkingMemory` fields | Nature |
|---|---|---|---|
| **Observation** (target facts) | Enricher → reducer (single-writer `apply_deltas`) | `hosts`, `credentials`, `findings`, `tools_used`, `defenses_observed` | dynamic, event-derived |
| **Definition** (the contract / mission) | Operator via `engagement.yaml` | `client`, `scope_*`, `rules_of_engagement`, `objectives` (id/goal/priority) | static, authored |
| **Interpretation** (where-we-are / what's-next) | **Future AI assistant** | `current_phase`, `objectives[].status`/`progress_pct`, `kill_chain_status` | dynamic, reasoned |

This is already partly encoded: `WorkingMemoryDeltas` (`domain/memory.py`)
deliberately excludes objectives/phase — *"objectives/phase (interpretation) …
are intentionally absent"*. The enricher writes observation, never
interpretation. The interpretation fields exist in the model, will be seeded
clean here, and **await their writer (the assistant)**.

**Implication for this phase:** we fix observation (Group A), seed definition
(Group C), clean up a dead model (Group B), and repair the deterministic
session path (Group D). We do **not** build any interpretation logic — no
deterministic phase-deriver, no objective-progress tracker. Those are the
assistant's, deferred to its own brainstorm.

## 3. Cross-cutting design constraints

These apply to every group and are non-negotiable acceptance criteria:

1. **No hardcoded victim/machine data.** Every username/host used for grounding
   or validation is derived at runtime from live working-memory state
   (`sessions`, `credentials`) or the enriched beacon's own identity. Nothing is
   baked into source. (This phase also *removes* an existing violation — see §6.2.)
2. **Deterministic-first.** The LLM does only the irreducibly fuzzy work
   (extracting facts from arbitrary text — which regex could not, per B2). Every
   guard around it — dedup, structural validation, the username-collision
   backstop — is deterministic, testable, and O(1). Maximize the deterministic
   surface; minimize the LLM surface.
3. **C2-portable.** All fixes are daemon-side, operating on canonical wire events
   + working memory. They know nothing about Cobalt Strike or Aggressor. The only
   C2-specific component is the event-producing adapter (the `.cna` for CS; a
   future Sliver adapter would emit the same canonical events). A C2 whose adapter
   populates `beacon.initial` with host/user gets the same grounding + backstop +
   structural rules with zero daemon changes.
4. **Stateless enricher + injected context.** The enricher LLM stays stateless
   (one independent `extract()` call per output chunk; no conversation thread).
   Statelessness ≠ context-free: we inject the small per-beacon grounding fresh
   each call. Statefulness belongs in the interpretation layer (the assistant),
   not the extractor. This composes cleanly with B3's deterministic dedup (a
   duplicate output re-extracts identically and is dropped before re-emit).

## 4. Decomposition and order

Four root-cause groups, executed **D → A → C → B**. The order is driven by one
real dependency plus impact:

- **D first** because Group A's grounding needs the active beacon's user, which
  lives in `sessions` — and Group D is what re-seeds sessions reliably. With
  empty sessions, A's grounding has no source.
- **A second** — highest assistant-impact (junk host/cred directly corrupt the
  network/credential picture the assistant reasons over).
- **C third** — gives the assistant operation-awareness (mission/ROE), but does
  not block A.
- **B last** — cosmetic (a dead Inspector section); lowest impact.

Each group is an independent plan with its own branch off `dev-juan`, written
and executed sequentially (one merges before the next is written, so the
substrate each plan reads is the merged previous state).

---

## 5. Group D — session re-seed + B3 regression fix (#107)

### Problem (verified)

`sessions` is populated by `beacon.initial → WorkingMemoryReducer._on_beacon_initial
→ upsert_session` (idempotent: replaces by `bid`). The `.cna` already backfills
beacon.initial for pre-existing beacons on script load
(`adapters/cobaltstrike/redrobin.cna:437-443`, `foreach beacons()`).

**B3 made this worse.** B3's `derive_key` (`application/dedup_keys.py`) dedups
`beacon.initial` with key `(bid, beacon.initial)`. After a working-memory clean,
the backfill's re-emitted `beacon.initial` is **dropped by the dedup gate** (the
bid is already in the persisted key set) → the session never re-seeds. This is
the `sessions: []` observed in the B3 smoke.

Deduping `beacon.initial` gains almost nothing — `upsert_session` is idempotent,
so re-processing is harmless — but it breaks the re-seed and creates a latent
footgun where dedup state and working-memory state diverge.

### Fix

In `derive_key` (`application/dedup_keys.py`), **remove `beacon.initial` from the
dedup'd path** — return `None` (pass-through, no dedup) for it. Command events
(`beacon.command_input/output`, `opsec.verdict`) keep their composite-key dedup;
only `beacon.initial` becomes pass-through.

Rationale: `beacon.initial` is idempotent at the reducer (upsert replaces), so a
reconnect flood of beacon.initial events causes only N harmless redundant upserts
+ N cheap snapshot re-emits — not the LLM-cost / finding-pileup harm that justifies
deduping command output. Correctness (re-seed works) wins over a negligible
dedup saving.

### Scope notes

- The backfill fires on `.cna` load/reconnect. After a working-memory clean, the
  operator triggers re-seed by reloading the `.cna` (already in the smoke runbook).
  Group D ensures that re-emit is **not dropped**; triggering it remains the
  operator action (the daemon has no callback into CS).
- No `.cna` change. No reducer change. Single change in `derive_key` + tests.

### Files

- Modify: `redrobin/application/dedup_keys.py` (`derive_key`: drop `beacon.initial`).
- Modify: `tests/unit/test_dedup_keys.py` (assert `beacon.initial` → `None`).
- Add: an integration test that a re-POSTed `beacon.initial` is NOT deduped and
  re-seeds the session (extend `tests/integration/test_events_router_dedup.py`).

---

## 6. Group A — extraction validation (#105 + #114)

Two defects, one root cause: the enricher LLM emits junk and nothing rejects it.
`WorkingMemoryDeltas.from_payload` (`domain/memory.py`) only requires `name`
(host) / `username` (cred) — no structural validation; `apply_deltas`
(`infrastructure/persistence/working_memory_repo.py`) merges whatever passes.

### 6.1 #114 — credential with no secret (deterministic rule)

A credential dict with no `secret` **and** no `ntlm_hash` is not a credential —
it is a username observation. Drop it.

- Fix: in `WorkingMemoryDeltas.from_payload`, when building a `Credential`, skip
  any entry lacking both `secret` and `ntlm_hash`.
- Fully general: structural, mentions no machine. `admin:P@ssw0rd` (has secret) →
  kept; `CORP\jdoe` (no secret) → dropped.

### 6.2 #105 — username extracted as host (grounding + backstop)

Structurally, a username (`JCSPI`) looks exactly like a hostname (`JCSP`). The
only signal that it is junk is *it is a known username*. The fix is three layers,
all derived from live state:

**Layer 1 — de-personalized static prompt.** The enricher system prompt
(`infrastructure/llm/anthropic_client.py:AnthropicEnricherClient._SYSTEM_PROMPT`)
currently hardcodes `'jcspi'` (the operator's real username) as a not-a-host
example — a §3.1 violation. Replace it with a generic, victim-neutral example
(e.g. `'administrator'` / a `C:\Users\<name>` path component). Teaches the concept
without embedding any victim's data.

**Layer 2 — dynamic per-beacon grounding (primary).** Inject into each
extraction call's prompt the **current beacon's own user and host**, derived at
runtime from `sessions[bid]`: *"This beacon's host is `<host>` and user is
`<user>` — do NOT emit either as a newly-discovered host."* O(1) payload (two
values); changes per beacon and per engagement. Nothing hardcoded.

**Layer 3 — structural backstop (deterministic safety net).** In the merge path,
drop an incoming host whose `name` exactly matches (case-insensitive) any known
username in live working memory (`sessions[*].user`, `credentials[*].username`).
This is a set-membership check in deterministic code — **not** injected into the
prompt — so it stays O(1) per host and the prompt does not grow with engagement
size. A real host (not a username) is always kept.

Approach **Option 1** (operator-approved): grounding does the heavy lifting; the
exact-match backstop is a precise net with zero false-reject risk on real hosts
(a real hostname will not exactly equal a known username; the rare host-named-
after-a-user edge case is accepted). No fuzzy hostname-shape heuristics.

### Scalability (design requirement, not optional)

- Prompt-injected grounding = the current beacon's user/host only (O(1)). The
  large known-username set is used by the **deterministic backstop, not the
  prompt** — the prompt never grows with engagement size.
- The enricher reads its grounding slice from an **in-memory cache refreshed on
  `working_memory_snapshot` bus events** (the reducer already emits these, #146)
  — not a file read per call. Precedent: the enricher already caches `bid →
  command` from `beacon.command_input` (B1.5 T4); cache `bid → (user, host)` from
  `beacon.initial` the same way.
- The cost driver remains output volume (chunk count), already bounded by B1.5's
  chunking + concurrency semaphore. Grounding adds a fixed tiny per-call overhead.

### Backstop location (plan-level decision, recommendation)

The structural backstop has the full working memory available in `apply_deltas`
(the reducer is the single writer and loads the whole WM to merge). Recommend
running the username-collision reject there (or in `from_payload` with the known-
username set passed in). The grounding (per-beacon user/host) is the enricher's
job (it builds the prompt). The writing-plans step picks the exact split; both
satisfy single-writer (reading is unrestricted; only the reducer writes).

### Files

- Modify: `redrobin/domain/memory.py` (`from_payload`: #114 no-secret drop;
  optionally the host backstop if done here).
- Modify: `redrobin/infrastructure/llm/anthropic_client.py` (de-personalize the
  static prompt; accept/inject the per-beacon grounding).
- Modify: `redrobin/application/enrichment/enricher.py` and `enrichment/llm.py`
  (build the grounding slice; extend the `EnricherLLM.extract` contract to carry
  it; maintain the `bid → (user, host)` cache from `beacon.initial`).
- Modify: `redrobin/infrastructure/persistence/working_memory_repo.py` (`apply_deltas`
  host backstop, if located here).
- Tests: unit for the #114 rule + the host backstop; an enricher test asserting
  the prompt carries dynamic grounding and not a hardcoded username.

---

## 7. Group C — seed definition from engagement.yaml (#103)

### Problem (verified)

`rules_of_engagement`, `objectives`, and `current_phase` have no source. The
`Engagement` model (`application/engagement_loader.py`) carries only
`slug/name/in_scope/out_of_scope/summary/client`. `apply_engagement_scope`
(`application/working_memory_seed.py`) seeds only `client` + scope. So ROE and
objectives are always empty and `current_phase` is stuck at `"not_started"`.

### Fix — seed the static *definition*, leave interpretation clean

Per §2, the operator authors the static definition; the assistant owns the
dynamic interpretation.

- Extend the `engagement.yaml` schema with `rules_of_engagement: list[str]` and
  `objectives: list[{id, goal, priority}]`.
- Extend the `Engagement` dataclass (`engagement_loader.py`) + its `load()` to
  read them (safe defaults: empty list).
- Extend `apply_engagement_scope` (`working_memory_seed.py`) to project ROE +
  objective *definitions* onto working memory.
- **Leave interpretation fields clean and untouched**: `objectives[].status =
  "not_started"`, `progress_pct = 0`, `current_phase = "not_started"`,
  `kill_chain_status = {}`. No deterministic deriver. The Inspector then shows
  real mission context with honest "not started" progress until the assistant
  exists.

### Files

- Modify: `redrobin/application/engagement_loader.py` (`Engagement` + `load`).
- Modify: `redrobin/application/working_memory_seed.py` (`apply_engagement_scope`).
- Modify: a sample/`manual-smoke` `engagement.yaml` to include ROE + objectives
  (smoke fixture).
- Tests: `Engagement.load` parses the new fields; `apply_engagement_scope` seeds
  ROE + objective definitions and leaves interpretation fields at defaults.

---

## 8. Group B — delete the dead `Account` model (#106)

### Problem (verified)

`domain/memory.py` defines both `Account` (operator-curated by intent) and
`Credential` (enricher-derived). Nothing populates `accounts` → the Inspector
ACCOUNTS section (`tui/textual_ui/inspector.py`) is always empty. The thesis
evolved to *observe, don't make the operator do manual work*; `Credential`
covers the real need. **Decision: delete (YAGNI).**

### Fix

- Remove the `Account` dataclass and the `accounts` field from `WorkingMemory`
  (`domain/memory.py`); fix `_from_dict` / serialization accordingly.
- Remove the `accounts` entry from `SECTIONS`, `_account_lines`, and the
  `section_counts` entry in `tui/textual_ui/inspector.py`.
- Remove any other `accounts` references (grep-prove zero remaining) — no dead code.
- Tests: update any snapshot/serialization/inspector tests that referenced
  `accounts`.

---

## 9. Non-goals

- No interpretation logic (phase advancement, objective-progress tracking,
  kill-chain derivation) — the assistant's, deferred.
- No manual account-curation feature (Account is deleted, not rebuilt).
- No fuzzy hostname-shape heuristics for #105 (exact-match-username only).
- No change to the cloud trust boundary (full-cloud is the accepted v0.1
  tradeoff; local models are the v2.0 answer).
- No Sliver adapter (this phase only ensures the daemon-side fixes are C2-portable
  for when one is built).

## 10. Test plan (per group)

- **D:** unit — `derive_key(beacon.initial)` returns `None`. Integration — a
  re-POSTed `beacon.initial` is not deduped and re-seeds the session.
- **A:** unit — `from_payload` drops a secret-less credential and keeps one with
  a secret; the host backstop drops a name matching a known username and keeps a
  real host. Enricher — the extraction prompt carries dynamic per-beacon
  grounding and contains no hardcoded username.
- **C:** unit — `Engagement.load` parses ROE + objectives; `apply_engagement_scope`
  seeds them and leaves interpretation fields at defaults.
- **B:** grep-prove zero `accounts` references remain; updated tests green.
- All groups: full CI gate (ruff / ruff format / mypy domain+application /
  import-linter / pytest), no regressions.

## 11. Acceptance criteria (live smoke, per group)

On a clean `manual-smoke` against a real beacon:

- **D:** after a working-memory clean + `.cna` reload, SESSIONS shows the active
  beacon.
- **A:** the operator's username does not appear as a host; a username-only line
  does not appear as a credential; a real credential with a secret still lands.
  The enricher prompt (verified by inspection) carries the live beacon's user/host,
  not `jcspi`.
- **C:** the Inspector ROE + OBJECTIVES sections show the engagement.yaml content;
  progress/phase honestly read "not started".
- **B:** the Inspector has no ACCOUNTS section; CREDENTIALS is unaffected.

## 12. Risks

- **A grounding depends on populated sessions** → mitigated by ordering D first.
  If sessions are empty for a beacon, the per-beacon grounding degrades but #114
  (structural) is unaffected and the backstop still works once any session/cred
  is known.
- **A backstop edge case** — a real host literally named the same as a known
  username would be wrongly dropped. Accepted as rare; exact-match keeps the risk
  minimal vs fuzzy heuristics.
- **C schema change** — older `engagement.yaml` files without ROE/objectives must
  still load (safe-default to empty); covered by `load()` defaults.
- **B deletion** — must grep-prove no remaining `accounts` reader before deleting
  (avoid a KeyError on an old working_memory.json that still has an `accounts`
  key; `_from_dict` should tolerate-and-ignore an unknown legacy key).

## 13. References

- Writer-split principle + locked decisions: memory `project_red_team_copilot.md`
  (ARCHITECTURE PRINCIPLE 2026-05-28).
- B3 (the dedup whose `beacon.initial` handling Group D revises):
  `docs/superpowers/specs/2026-05-28-cna-event-dedup-design.md`.
- Enricher pipeline (B1/B1.5): `redrobin/application/enrichment/`.
- The future assistant (parked brainstorm) is the consumer this phase serves.
