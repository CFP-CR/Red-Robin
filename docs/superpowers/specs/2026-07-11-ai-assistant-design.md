# AI Assistant — Design (brainstorming CLOSED 2026-07-16 · ready for writing-plans)

- **Status:** ✅ **Brainstorming COMPLETE** (closed 2026-07-16, branch `feat-ai-assistant` off `dev`). All §6 design decisions are closed — this is the approved design. A few items are explicitly deferred to implementation-time (`#advisor-dock` UX, LLM-boundary redaction, lesson-storage impl) — noted in place, not blockers. **Next per AI-DLC: `writing-plans`** (executable plan in `docs/superpowers/plans/`) → `auditing-plans` → `subagent-driven-development`.
- **North star:** augment-not-replace, operator-better. NOT driven by the DEF CON demo/timeline.
- **Scope bar (operator-confirmed 2026-07-11):** **production-grade and COMPLETE — NOT MVP / v1→v2 phasing.** The design targets the full production-quality assistant (all modes, all quality: never-crash, degraded, cost-controlled, observable, tested, OPSEC-safe). It is *built* incrementally (AI-DLC slices, each production-quality), never shipped as a stripped-down first version.
- **Pillars — the bar every decision is judged against:** **① Scalability** — new capabilities plug into the skeleton's seams (tool registry / `AgentLLM` Protocol / typed events), no re-architecture (§2, §4, §6.F). **② Maintainability** — clean layered arch, one ABC, facades, native-not-Agent-SDK, never-crash / degraded-None (CONTRIBUTING + `.claude/rules/architecture.md`). **③ Clarity** — code a human dev reads and understands, *and* the assistant itself **explains the why** (the thesis, §1). Any decision that trades these three away for speed is off-spec.

## 1. What the assistant IS

The **operation-aware AI copilot** — one brain, several capabilities:

- **Reactive + state-aware:** reacts to the operator's operation output, grounded in the engagement state it reads from the memory layers.
- **Recommends next steps WITH the WHY + OPSEC-awareness.** Not "do X" — the tradecraft rationale + the detection risk (grounded in `defenses_observed`). A recommendation without the why is outsourcing, not augmenting.
- **Advisory-only: proposes, never acts. The synchronous pre-flight GATE is ELIMINATED** (the pre-send ALLOW/BLOCK/ESCALATE hold — agreed in the 2026-05-27 pivot). Nothing is blocked/held before it sends. What stays = the `.cna` **capture/interception** (the data moat feeding the assistant, the only Sliver-portable part). ALL reasoning is advisory (ESCALATE = "flagged for attention", not a hold). Operator decides & executes. Read-only by construction (emits recommendations, never dispatches).
- **Selective (signal, not noise):** speaks when it has something worth saying, stays quiet otherwise.
- **Both advisory AND interpretation-writer** (one brain, two outputs):
  - *Recommendation (lower-stakes):* `advisor.recommendation` → `#advisor-dock`.
  - *Interpretation (higher-stakes):* writes `current_phase` / `objectives[].status`+`progress_pct` / `kill_chain_status` (writer-split's 3rd role). Mutates the shared picture the assistant re-reads → compounding-error risk → publish `engagement.milestone` → the reducer writes it (single-writer, never writes WM directly); attributed as "the assistant's read", reversible; **propose-and-confirm** for big changes.
- **Bidirectional + learns from the operator:** operator pushes back ("that's not a good move") → the assistant captures a **context-scoped lesson** (this operator, this situation, this reason — NOT a blanket "X is bad") that it **reads back** when reasoning. This is Claude Code's **auto-memory** pattern, and it **closes the gap** where L1 lessons are written but never read. Auditable/editable. Uses the TUI Chat pane + `operator.question`/`operator.answer`.

Follows **Claude Code patterns** (not the harness): agent-with-tools; progressive disclosure over the 5 memories; auto-memory-that-learns; skills = tradecraft playbooks; subagents = the "deep on-demand" second speed vs fast Haiku commentary; human-in-the-loop.

## 2. Architecture decision: NATIVE, not the Agent SDK (decided)

- **Today (verified):** raw `anthropic` SDK, single-shot `messages.create` at 3 sites (Layer B sonnet-4-6; enricher + summarizer haiku-4-5), prompt caching **at Layer B + enricher only (summarizer deliberately uncached)**, behind `LLMProvider` ABC + narrow Protocols. **Zero Agent SDK, zero tool loops.**
- **Decision:** the assistant is the FIRST agentic/tool-calling component. Build NATIVE — a narrow `AgentLLM` **Protocol** + a tool-calling loop via Anthropic's **Tool Runner** (`client.beta.messages.tool_runner`, wrapped behind the Protocol so the beta API is insulated) on the SAME `anthropic` client + caching + bus + repos. **NOT** the Agent SDK harness (own client/sessions/`~/.claude`/MCP → duplicates + fights the clean-arch). **⚠️ Plan task (audit D1):** `tool_runner` is confirmed in the locked `anthropic==0.100.0` (`uv.lock`), but `pyproject.toml`'s floor `>=0.60.0` is too weak — a fresh resolve could land on a 0.6x lacking `tool_runner`. The plan must **raise the `pyproject.toml` floor** to the version that introduced `beta.messages.tool_runner` (verify via changelog) so a collaborator/CI `uv sync` can't get a broken resolve.
- **3-layer framing:** leverage Anthropic at layer 1 (SDK) + layer 2 (Tool Runner loop); build layer 3 (the DOMAIN — tools over memory, prompts, advisory/interpretation split, feedback-learning) ourselves. Effort → 100% domain.
- **Follows the rules by construction:** one-ABC (AgentLLM=Protocol, not a 2nd ABC), 4 import-linter contracts, facade pattern, bus-first, single-writer, never-crash, degraded-None. The Agent SDK would strain these; native keeps them.

## 3. The existing LLM components — their fate

| Component | Role | With the assistant |
|---|---|---|
| **Enricher** (Haiku) | extracts facts from `beacon.command_output` → **observation** (`working_memory.deltas` → reducer) | **Unchanged.** Observation-writer sibling; the reference pattern the assistant copies. |
| **Summarizer** (Haiku) | compresses episodes → L5 `ContextSummary` | **Unchanged.** Feeds context the assistant reads. |
| **Lesson promoter** (NOT LLM) | aggregates verdicts → L1 `PromotedLesson` | **Unchanged**, but the assistant becomes its **first reader** (closes the L1-read gap). |
| **Layer B** (Sonnet) | per-command ESCALATE judge; produces `suggestions[]` (currently dropped before the bus) | **DECIDED: absorbed** → its per-command reasoning becomes the assistant's fast reactive speed (advisory). |

**The synchronous pre-flight gate is eliminated (see §1).** So: **Layer B** is absorbed into the assistant (advisory, not a gate verdict). **Layer A** (fast deterministic Sigma rules) loses its only real value (the sync BLOCK) → it is either removed or demoted to a cheap advisory signal the assistant can use as an input — NOT a gate. The `.cna` **capture** stays. The writer-split keeps the writers non-overlapping: enricher = observation, operator/yaml = definition, assistant = interpretation. No refactor of enricher/summarizer/promoter.

**Why enricher/summarizer are NOT merged into the assistant (decided 2026-07-14, verified in code).** They are *not* "agents" in the assistant's sense — they are **single-shot LLM transforms** (`EnricherLLM.extract` is one call, no tool loop; `enrichment/llm.py`), on a **high-frequency, critical, always-on** path. The assistant is a **selective, expensive, agentic (tool-loop)** reasoner. Keep them separate because:
1. **Frequency × cost** — the enricher fires on **every** `beacon.command_output` (`enricher.py:83-86`) on cheap Haiku. Doing that inside the assistant = a full Opus/Sonnet tool-loop per command → cost/latency blow-up.
2. **Writer-split integrity** — the enricher is the *independent observer* that gives the assistant objective facts to reason over. One brain doing extraction **and** interpretation = the assistant reasoning over facts it wrote with its own spin → compounding error.
3. **Never-crash / degraded** — the enricher catches every exception (`enricher.py:87-88`) and runs degraded without an API key (`llm.py:36-38`); WM population must survive the assistant being down/rate-limited.
4. **Latency** — WM must be fresh fast (one Haiku call), not gated behind a slow tool-loop.
They already **share** client/bus/repos/patterns (why they co-locate in `application/agents/`) — cohesion without collapse. **The assistant CONSUMES what they produce; it does not replace them.** (Note: only the assistant is a true agent; enricher/summarizer are LLM transforms — the `agents/` folder name is loose.)

## 4. File / folder structure — `application/agents/` (cohesion within the layer)

All autonomous AI agents grouped under **`application/agents/`** for clarity, WITHOUT breaking the layered import-linter contracts (it stays `redrobin.application.*`, so the 4 contracts hold unchanged). Pure types stay in `domain/`; Anthropic clients stay in `infrastructure/llm/`. **Layer B stays in the verdict pipeline** (it's a synchronous gate engine, not an autonomous agent).

```
redrobin/
├── domain/assistant.py                     # pure types: Recommendation, Lesson
├── application/
│   ├── agents/                             # ← ALL autonomous AI agents, together (clarity)
│   │   ├── enricher/                       #   (moved from application/enrichment/)
│   │   ├── summarizer.py                   #   (moved from application/summarizer.py)
│   │   └── assistant/                      #   ← new
│   │       ├── assistant.py                #     bus-subscriber consumer + cycle orchestration
│   │       ├── agent.py                    #     the agentic loop (Tool Runner underneath)
│   │       ├── tools.py                    #     retrieval tools over L1–L5 / WM / KB
│   │       ├── triggers.py                 #     "do I have signal worth speaking?" gate
│   │       ├── lessons.py                  #     feedback → context-scoped lesson → store
│   │       └── llm.py                      #     AgentLLM Protocol (like the enricher's llm.py)
│   ├── assistant_lifecycle.py              # facade (like longterm_lifecycle)
│   └── event_bus.py, verdict_pipeline.py, layer_b_engine.py, ...   # unchanged (Layer B stays here)
├── infrastructure/llm/anthropic_client.py  # + AnthropicAgentClient (AgentLLM, Tool Runner)
├── api/lifespan_helpers.py                 # + start_assistant(app, settings)
└── tui/textual_ui/advisor.py               # advisor.recommendation handler → #advisor-dock
```

**Also touched — the tree above is the target LAYOUT (new + moved files), NOT the exhaustive change manifest.** Beyond the new/moved files, these existing files are MODIFIED:
- `application/working_memory_reducer.py` — add a `_dispatch` case for the `engagement.milestone` interpretation channel (the reducer's `_dispatch` is an if/elif on event type, `working_memory_reducer.py:111`).
- `tui/textual_ui/app.py` — register an `advisor.recommendation` frame handler, mirroring the `working_memory_snapshot` handler at `app.py:173`, + surface the dock.
- `tui/textual_ui/theme.tcss` — un-hide `#advisor-dock` (currently `display: none` near `theme.tcss:50`).
- `infrastructure/config.py` — assistant `Settings` (model / trigger cadence / token budget / enable / degraded), mirroring `enrichment_model` + `allow_degraded_layer_b` (`config.py:63,84`).
- `domain/events.py` (+ possibly a `protocol/schemas/v0.1/events/` entry) — register the `advisor.recommendation` event type (`engagement.milestone` already exists at `events.py:121`; `advisor.recommendation` does NOT) — **confirm in plan**.
- `pyproject.toml` — raise the `anthropic` floor above `>=0.60.0` (`pyproject.toml:10`; audit D1).
- **enricher/summarizer MOVE ripples** to every import site — `api/lifespan_helpers.py` uses `start_enricher`/`start_l5_summarizer` (`lifespan_helpers.py:496,446`), plus their tests (`tests/integration/test_enricher.py`, `test_l5_summary.py`, `tests/unit/enrichment/*`).
- Plus **NEW tests** under `tests/unit/` + `tests/integration/`.

The exhaustive per-file change list is the `writing-plans` deliverable.

**Sequencing (decided):** the move of the existing agents into `application/agents/` is a SMALL, INDEPENDENT refactor of working/tested code (needs none of the assistant's open decisions). Do it as its OWN atomic change — a `refactor:` commit, acceptance = the 5 CI gates (deterministic logic → no beacon smoke) — BEFORE the assistant lands in the ready home. It is NOT "step 1" of the assistant feature (keep the reorg and the new-feature build as separate, independently-reviewable changes).

## 5. Pre-wired seams (from the 2026-07-11 code discovery)

- **Output — recommendations:** `#advisor-dock` exists (hidden); one `@REGISTRY.register("advisor.recommendation")` + flip display. `tui_ws` forwards all bus events → zero changes.
- **Output — interpretation:** `engagement.milestone` event + schema + reducer merge-preservation EXIST; nothing publishes it (add producer + a reducer dispatch case).
- **Input:** the `EventBus` (fan-out; subscribe + filter). Observable: `beacon.*`, `working_memory.deltas` (dotted) + `working_memory_snapshot` (**underscore** — the codebase is inconsistent; match each name exactly), `opsec.verdict`, `enrichment.completed`/`failed`.
- **Live-state-into-reasoning:** Layer B has a reserved `dynamic_state=""` — unused.
- **Advisory content almost exists:** Layer B's `suggestions[]` are dropped before the bus.
- **Memory:** L2/L3/L5 fully built; **L1 written-but-never-read** (feedback-learning closes it); **L4 tradecraft graph NOT built** (grounding-in-tradecraft is via the KB/RAG: `KBService.retrieve`, one flat numpy Voyage index — scale ceiling, v0.2 pgvector). **Naming caveat (audit 2026-07-16):** only `L5` (`L5Summarizer`) and `Layer A/B` are real code identifiers; `L1`–`L4` live only in docstrings — treat them as design vocabulary, not code symbols.
- **KB is the built knowledge/tradecraft layer (verified in code 2026-07-14).** `KBService.retrieve` (Voyage embeddings + `VectorStore` numpy cosine, persisted `.npz` at `~/.redrobin/shared/kb-index/`) over **curated markdown** (`kb add`/`kb ingest`; ID e.g. `vault/Techniques/mimikatz.md#chunk_0`) — the *cross-op, authored* knowledge axis, distinct from the *auto-captured engagement* stores (L2/L3/L5/Journal). It is what L4-the-graph was meant to do, done as RAG → **L4 is redundant: retire / mark superseded by the KB** (decision separate from the assistant). Today `KBService` is *"called by Layer B for RAG context"* → since Layer B is absorbed, **the assistant inherits the KB as a first-class retrieval backend that already works.**

## 6. Design decisions (ALL CLOSED 2026-07-16 — the brainstorming record)

> Every item below is **decided** (✅). A handful of sub-points are explicitly deferred to implementation-time (marked ⏸️ / "plan task") — those are *documented decisions to make later*, not open design questions. "OPEN/next" labels inside this section are historical.

**A. The brain:**
- ✅ **Trigger model — DECIDED: three modes, all in** (production-grade, not phased), framed by *who initiates*:
  - **Reactive** — the assistant speaks, triggered by an **event** (your command / output).
  - **Proactive** — the assistant speaks, **self-triggered** on its own cadence (watch-your-back — the value neither of the others catches).
  - **Conversational** (renamed from "on-demand") — **you** initiate a dialogue: you **ask** ("what's my path to DA?") OR **correct** ("that's too loud") — and correcting is **where it learns** (unifies the pull-query + the feedback/learning loop in one mode).
  Reactive + proactive = the assistant pushes (differ only in the trigger); conversational = the operator pulls/dialogues. **Speed** (fast Haiku vs deep Sonnet) is a **separate axis** from the trigger. All push behind a hard **"signal-not-noise"** gate.
- ✅ **Relationship to Layer B — DECIDED: absorbed** (its per-command reasoning → the assistant's fast speed, advisory). Synchronous gate eliminated (§1).
- The two speeds + models (Haiku reactive vs Sonnet/Opus deep); context per cycle (working-set + retrieval). *(still to detail)* — **constraint from the research:** the two speeds must NOT be a model-swap inside one Tool Runner loop (prompt caches are model-scoped → a mid-loop swap invalidates the whole prefix). The cheap/fast speed runs as a **sub-agent** returning a distilled ~1–2k-token summary (also Anthropic's sub-agent pattern). Also: the runner does not auto-resume `pause_turn` — only relevant if a pull tool is backed by a *server-side* tool (ours are local retrieval, so N/A today).

**B. Tools:**
- ✅ **Operational Journal — DECIDED: build it** (6th layer, *engagement* axis). Raw `(command,output)` transcript + semantic retrieval; the other layers are distilled/lossy, the Journal is the verbatim record so the assistant can re-read the exact text of any past moment. Material **already exists in `events.jsonl`** — but as **two separate events** (`beacon.command_input.command/raw` + `beacon.command_output.output`) correlated by `correlation_id`, NOT a combined tuple, so the Journal must **join them by `correlation_id`** (the pattern the enricher's `_cmd_by_corr` cache already uses) → the layer = **index it (Voyage) + a retrieval tool**, not new capture.
- ✅ **KB — DECIDED: recognized as the already-built knowledge layer** (verified, §5); the assistant treats `KBService.retrieve` as a first-class tool backend (inherited from Layer B's absorption).
- ✅ **Push/pull split — DECIDED, validated against Anthropic's official context-engineering guidance** (research 2026-07-14). It IS Anthropic's endorsed **hybrid** strategy: a small resident working-set + just-in-time retrieval over lightweight identifiers.
  - **PUSH** (resident working-set, small + token-bounded): **L2 working-memory snapshot** (current state) + **L5 compressed summary** ("story so far"). = Anthropic's "working memory" + compaction pattern.
  - **PULL** (on-demand retrieval tools): `search_journal` (raw transcript), `kb_retrieve` (tradecraft — backend exists), `search_episodes` (L3), `get_lessons` (L1). Textbook just-in-time; 4 tools = within "few thoughtful tools".
  - **Refinements to apply at build (from the research):**
    1. **Caching — do NOT co-locate the mutating snapshot with the cached prefix.** Cache only the FROZEN prefix (system + tool defs + static tradecraft), `cache_control` breakpoint at its end (order tools→system→messages, static-before-dynamic, prefix-match). Put the volatile snapshot/summary AFTER the breakpoint (append as `role:"system"` on Opus 4.8). It's a cache *write* when it changes — not "kept warm" (my earlier framing was wrong).
    2. **Context-rot guard:** cap the resident set tight — recall degrades as resident tokens grow.
    3. **Tools:** descriptions must be prescriptive about *when to call* (Opus 4.7/4.8 under-reach for tools); consider consolidating `search_journal`+`search_episodes` (look overlapping); return high-signal excerpts not dumps; cap output (~25k tok); `response_format` concise/detailed enum; actionable errors.
    4. **Tool Runner hook:** add `cache_control` to large retrieval results via `generate_tool_call_response()`; gate destructive/interpretation-writes via the same hook (no hand-rolled loop).
  - Sources: `anthropic.com/engineering/{effective-context-engineering-for-ai-agents, writing-tools-for-agents}`; `platform.claude.com/docs` prompt-caching + tool-runner.

**C. Outputs:**
- ✅ **Recommendation shape — DECIDED: 5 mandatory parts** (each embodies the thesis — advice, not command). New rich type `Recommendation` in `domain/assistant.py` (the existing `suggestions: list[str]` on `Verdict`/`OpsecVerdictEvent` is too thin), emitted as a new `advisor.recommendation` bus event (no functional impl yet — appears only as a docstring example in `frames.py` + a throwaway test fixture in `test_frames.py`; no producer/handler/type, so no collision — verify that test when wiring the real handler):
  1. `suggested_action` — the concrete next step (the WHAT).
  2. `rationale` — the tradecraft WHY given engagement state (coaching core; without it it's outsourcing).
  3. `opsec_risk` — detection risk: reuse `Severity` (INFO/WARN/DANGER) + a short reason **grounded in `defenses_observed`**.
  4. `confidence` — how sure + explicitly what it is NOT sure about (operator calibrates, never blind-trusts).
  5. `references` — the grounding it drew on (WM facts / KB chunks / Journal moments / L3 episodes); mirrors `operator.answer.citations[]` → verifiable + auditable (anti-hallucination).
  Plus `correlation_id?` — attaches to the triggering command (reactive) or `None` (proactive). Worked example lives in the brainstorm (Kerberoast `svc_sql`: confirmed-vs-bet honesty, CrowdStrike flagged, sources cited).
- ⏸️ **`#advisor-dock` — functional rendering is IN slice 1; only polish is deferred.** Slice 1 MUST un-hide the dock + register an `advisor.recommendation` frame handler + render the 5-part card (plainly) — otherwise the slice isn't end-to-end / visible (would violate §6.F). **Deferred** = visual polish (colors / spacing / animation) + interaction affordances that belong to LATER capabilities (dismiss / ask / ack → the conversational slice; the confirm affordance → the interpretation-write slice). So the assistant is **VISIBLE from slice 1 — "plain then polished", never invisible.** Red Robin's UI is a **Textual TUI** (widgets + `.tcss`, in-repo, layout-tested) — modest, not a separate frontend.
- ✅ **Interpretation-writing mechanics — DECIDED.** Two tiers by stakes: **auto-write** soft/reversible reads (`current_phase` nudge, `progress_pct` bump), labeled "assistant's read" + reversible; **propose-and-confirm** for changes that *close/commit* (objective → COMPLETE, kill-chain goal reached). The confirm is **binary (yes/no), async, NON-BLOCKING** — the assistant proposes a *pending* change and ends (never freezes waiting, coherent with the eliminated sync gate); the operator resolves it whenever, and the beacon is never held. **Continuity lives in the memory layers, NOT a live agent session** — the confirm references the proposal by id (`question_id`/`milestone_id`), and the next cycle reconstructs context from memory seeing the resolved state (the layers ARE the session state). Reuses the existing `operator.question`/`operator.answer` channel (shared with conversational + learning). Escalating a confirm into a typed reply = a correction = feeds the learning loop. Mechanically: never writes WM directly → publishes `engagement.milestone` → reducer (single-writer, like the enricher). The *visual* of the confirm affordance = UI, deferred.

**D. Learning loop — DECIDED:**
- **Trigger:** operator correction via the conversational mode (`operator.question`/`operator.answer` + Chat pane) — the highest-signal source.
- **Lesson shape: context-scoped, NEVER blanket** — captures *(what-to-do-differently + the context it applies in + the why + provenance/date)*. E.g. NOT "mimikatz bad" but "EDR observed (CrowdStrike) → prefer `comsvcs.dll` minidump; operator flagged mimikatz too loud; engagement Acme 2026-07-14."
- **Scope tag — the production-grade choice (single-bag rejected as MVP): `engagement` vs `operator`.** `engagement` = quarantined, dies with the op (prevents cross-engagement **contamination** — a honeypot fact poisoning the next client). `operator` = persists cross-engagement (**cumulative** learning about the operator, so they never re-teach). Inferred at capture, operator-adjustable. Single-bag forces either contamination (all-permanent) or amnesia (all-die) — both non-production.
- **Read-back:** via the `get_lessons` pull tool (already in the tool set) — **closes the L1-written-but-never-read gap**. A new engagement loads only `operator`-scoped lessons (small, relevant set — context-rot hygiene).
- **Storage (impl detail, deferred):** likely extend L1 as a new lesson-source (operator-correction) alongside the existing verdict-aggregation `PromotedLesson`.

**E. Safety / quality:**
- ✅ **Settled (cross-cutting, decided elsewhere):** anti-hallucination via the mandatory `references` field + reasoning over retrieval (not memory) + `confidence` (confirmed vs bet); **scope-safety** = advisory-only, never dispatches to the beacon (read-only by construction); **never-crash + degraded-None** (no API key → assistant silent, daemon boots); **cost** = Haiku for cheap + selective signal-not-noise trigger + token-capped tools; **observability** = `assistant.*` bus events.
- ⏸️ **DEFERRED to an implementation-time decision (operator, 2026-07-14): exactly what the assistant sends to the LLM (redaction at the boundary).** Not a thesis-level blocker — recorded here as a decision to make when building the LLM-boundary slice. Leading candidate (NOT locked): send secret **metadata** (account / kind / location) but never the secret **value** (hash/password/key), redacted/tokenized at the boundary → OPSEC (client secrets don't leave to a third party) with no loss of reasoning power. Existing infra to build on: `value_redacted_flag` on `credential.observed`.

**F. Scope — DECIDED: first slice = skeleton-complete + minimal-live-capability.**
- **Principle (production-grade + scalable):** the first slice is **narrow-in-capability but complete-in-architecture** — it lays the FINAL extensible seams (`AgentLLM` Protocol, **tool registry**, push/pull memory contract, typed-event output, degraded lifecycle wiring), each production-quality, **PROVEN end-to-end by one real capability**. NOT empty scaffolding-then-features (the unvalidated horizontal anti-pattern), NOT a throwaway spike, NOT a heavy framework. The capability validates the skeleton; later slices **plug in** (register a tool / add a trigger / add an output handler) without re-architecting.
- **The first slice = "Reactive grounded recommendation":** trigger on `enrichment.completed` (already fires) → the Tool Runner agent loop reads the push working-set (L2+L5, built) → calls `kb_retrieve` (KB backend exists) → emits a 5-part `advisor.recommendation` to the bus → the pre-wired `#advisor-dock` renders it. Production-quality: never-crash, degraded-None, tested, grounded.
- **Deferred to later slices** (each plugs into the skeleton): Operational Journal + its tool; proactive mode; conversational mode + learning loop; interpretation-write + propose-and-confirm; remaining pull tools (episodes / lessons); two-speeds / subagent; the deferred impl decisions (dock UX, LLM-boundary redaction).

## 7. Definition of Done (DoD)

**Per-slice DoD** — every slice must meet ALL:
- ✅ **The 5 CI gates green** — `uv run ruff check .`, `ruff format --check .`, `mypy redrobin/domain redrobin/application`, `lint-imports --config importlinter.cfg`, `pytest tests/` (per `CLAUDE.md`).
- ✅ **TDD (red→green)** — the failing test written first, verified red, implemented, verified green, and passing (CONTRIBUTING).
- ✅ **Never-crash + degraded-None** — no `ANTHROPIC_API_KEY` → the assistant is silent/None, the daemon still boots (mirror the enricher: degraded without a key `llm.py:36-38`, catches every exception `enricher.py:87-88`); an exception in a cycle → logged, daemon survives.
- ✅ **Observability** — `assistant.*` bus events emitted (§6.E).
- ✅ **End-to-end** — the slice's capability demonstrated end-to-end (a real capability validating the seams, not scaffolding — §6.F).
- ✅ **Operator smoke for the live surface** — anything on the **live surface** (the `#advisor-dock` render, the TUI, enricher/reducer over a real beacon) → an **operator smoke** (skill `smoke-test`); CI cannot exercise these (`development-workflow.md` §5).
- ✅ **Atomic conventional commits, no AI co-author trailer** (CONTRIBUTING).

**Global DoD** — the whole assistant is "done" when:
- All slices shipped; all **3 trigger modes** (reactive / proactive / conversational) working (§6.A).
- **Thesis satisfied** — advisory-only (never dispatches), explains the WHY, learns from correction, operator decides (§1).
- **OPSEC-safe** — the LLM-boundary redaction decision resolved (§6.E, §10).
- The **3 pillars** upheld — scalability / maintainability / clarity (Pillars).

## 8. Test strategy

Grounded in `development-workflow.md`'s two acceptance forms.

**(a) Deterministic logic → unit + integration; the 5 CI gates ARE the acceptance (no beacon).** Covers the agentic-loop orchestration, each retrieval tool, lesson context-scoping (engagement vs operator), the triggers / signal-not-noise gate, redaction, recommendation assembly → `tests/unit/…` + `tests/integration/…` (layout per CONTRIBUTING).

**(b) Live surface → operator smoke ONLY (CI can't drive these).** The dock rendering a real recommendation, the TUI, the reducer/enricher over real beacon output → skill `smoke-test` (`development-workflow.md` §5).

**Concrete test types:**
- **LLM boundary mocked** via the `AgentLLM` Protocol — a fake client in tests (like `EnricherLLM`; cf. `tests/unit/enrichment/test_llm.py`).
- **Per-tool tests** over a seeded repo (KB / Journal / L3 / L1 fixtures).
- **Lesson-scope tests** — `engagement`-scoped dies with the op; `operator`-scoped persists (§6.D).
- **Trigger-gate tests** — signal-not-noise: speaks on signal, silent on noise (§6.A).
- **Degraded-mode test** — missing API key → None, daemon boots.
- **Never-crash test** — an exception in a cycle → logged, daemon survives.

`mypy --strict` covers the assistant's `domain/` + `application/` code (strict scope per CONTRIBUTING / `architecture.md`). Adapter tests are **separate** (`adapters/<name>/tests/`) and **N/A** here — the assistant is core, not an adapter.

## 9. Non-goals / out of scope

- **NOT autonomous** — advisory-only, never dispatches to the beacon (read-only by construction, §1).
- **NOT the Claude Agent SDK** — native Tool Runner behind the `AgentLLM` Protocol (§2).
- **NOT a synchronous pre-flight gate** — eliminated; all reasoning advisory (§1, §3).
- **NOT multi-user / persistent server sessions** — continuity lives in the memory layers, not a live agent session (§6.C).
- **NOT a web frontend** — Textual TUI only (§6.C).
- **Does NOT replace or refactor the enricher/summarizer** — it CONSUMES them (§3).
- **Does NOT build L4** — the tradecraft graph is retired, superseded by the KB (§5).
- **NOT polished UX in slice 1** — functional/plain dock, polish later (§6.C, §6.F).
- **The Journal is NOT new data capture** — it indexes the existing `events.jsonl` (§6.B).

## 10. Deferred decisions & still-to-detail

Each is a DOCUMENTED decision to make later — NOT an open design question; **none blocks slice 1.**

- ⏸️ **LLM-boundary redaction** — candidate: secret *metadata* yes, secret *value* never; build on infra `value_redacted_flag` on `credential.observed` (§6.E).
- ⏸️ **`#advisor-dock` visual polish + later-capability affordances** — dismiss / ask / ack → the conversational slice; the confirm affordance → the interpretation-write slice (§6.C).
- ⏸️ **Lesson storage impl** — extend L1 as a new lesson-source (operator-correction) vs a new type (§6.D).
- ⏸️ **D1 — raise the `pyproject.toml` `anthropic` floor** above `>=0.60.0` (`pyproject.toml:10`); the locked `uv.lock` = 0.100.0 has `tool_runner`, but a fresh resolve could land on a 0.6x lacking it (§2 audit D1).
- ⏸️ **L4 tradecraft-graph retirement** — a separate decision; the KB supersedes it (§5).
- ⏸️ **Two-speeds mapping** — which trigger → Haiku vs Sonnet; the deep speed = a **sub-agent**, not a mid-loop model-swap (caches are model-scoped) — already decided (§6.A).
- ⏸️ **Proactive-mode cadence** — the self-trigger interval + the watch-your-back signal (§6.A).
- ⏸️ **The signal-not-noise heuristic** in `triggers.py` (§4, §6.A).
- ⏸️ **Whether a recommendation's dismiss/ack feeds the learning loop** — weak signal (§6.D).

---

Related memory: `ai-assistant-thesis-first`, `ai-assistant-design-decisions`, `redrobin-project-journal-location` (in the spiedra-vault Claude auto-memory). Code discovery: 2026-07-11 (memory system, LLM+retrieval, bus+TUI+verdict, engagement+wiring maps).
