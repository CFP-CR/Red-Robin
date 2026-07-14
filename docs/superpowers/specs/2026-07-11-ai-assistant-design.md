# AI Assistant — Design (WIP, brainstorming in progress)

- **Status:** 🚧 WIP — brainstorming (started 2026-07-11, branch `feat-ai-assistant` off `dev`). This is a **living decisions log**, NOT yet an approved spec. Open decisions listed at the end; the final spec + implementation plan come after the brainstorming closes.
- **North star:** augment-not-replace, operator-better. NOT driven by the DEF CON demo/timeline.
- **Scope bar (operator-confirmed 2026-07-11):** **production-grade and COMPLETE — NOT MVP / v1→v2 phasing.** The design targets the full production-quality assistant (all modes, all quality: never-crash, degraded, cost-controlled, observable, tested, OPSEC-safe). It is *built* incrementally (AI-DLC slices, each production-quality), never shipped as a stripped-down first version.

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

- **Today (verified):** raw `anthropic` SDK, single-shot `messages.create` at 3 sites (Layer B sonnet-4-6; enricher + summarizer haiku-4-5), prompt caching, behind `LLMProvider` ABC + narrow Protocols. **Zero Agent SDK, zero tool loops.**
- **Decision:** the assistant is the FIRST agentic/tool-calling component. Build NATIVE — a narrow `AgentLLM` **Protocol** + a tool-calling loop via Anthropic's **Tool Runner** (`client.beta.messages.tool_runner`, wrapped behind the Protocol so the beta API is insulated) on the SAME `anthropic` client + caching + bus + repos. **NOT** the Agent SDK harness (own client/sessions/`~/.claude`/MCP → duplicates + fights the clean-arch).
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

**Sequencing (decided):** the move of the existing agents into `application/agents/` is a SMALL, INDEPENDENT refactor of working/tested code (needs none of the assistant's open decisions). Do it as its OWN atomic change — a `refactor:` commit, acceptance = the 5 CI gates (deterministic logic → no beacon smoke) — BEFORE the assistant lands in the ready home. It is NOT "step 1" of the assistant feature (keep the reorg and the new-feature build as separate, independently-reviewable changes).

## 5. Pre-wired seams (from the 2026-07-11 code discovery)

- **Output — recommendations:** `#advisor-dock` exists (hidden); one `@REGISTRY.register("advisor.recommendation")` + flip display. `tui_ws` forwards all bus events → zero changes.
- **Output — interpretation:** `engagement.milestone` event + schema + reducer merge-preservation EXIST; nothing publishes it (add producer + a reducer dispatch case).
- **Input:** the `EventBus` (fan-out; subscribe + filter). Observable: `beacon.*`, `working_memory.deltas`/`snapshot`, `opsec.verdict`, `enrichment.completed`/`failed`.
- **Live-state-into-reasoning:** Layer B has a reserved `dynamic_state=""` — unused.
- **Advisory content almost exists:** Layer B's `suggestions[]` are dropped before the bus.
- **Memory:** L2/L3/L5 fully built; **L1 written-but-never-read** (feedback-learning closes it); **L4 tradecraft graph NOT built** (grounding-in-tradecraft is via the KB/RAG: `KBService.retrieve`, one flat numpy Voyage index — scale ceiling, v0.2 pgvector).

## 6. OPEN decisions (still to decide in the brainstorming)

**A. The brain:**
- ✅ **Trigger model — DECIDED: three modes, all in** (production-grade, not phased), framed by *who initiates*:
  - **Reactive** — the assistant speaks, triggered by an **event** (your command / output).
  - **Proactive** — the assistant speaks, **self-triggered** on its own cadence (watch-your-back — the value neither of the others catches).
  - **Conversational** (renamed from "on-demand") — **you** initiate a dialogue: you **ask** ("what's my path to DA?") OR **correct** ("that's too loud") — and correcting is **where it learns** (unifies the pull-query + the feedback/learning loop in one mode).
  Reactive + proactive = the assistant pushes (differ only in the trigger); conversational = the operator pulls/dialogues. **Speed** (fast Haiku vs deep Sonnet) is a **separate axis** from the trigger. All push behind a hard **"signal-not-noise"** gate.
- ✅ **Relationship to Layer B — DECIDED: absorbed** (its per-command reasoning → the assistant's fast speed, advisory). Synchronous gate eliminated (§1).
- The two speeds + models (Haiku reactive vs Sonnet/Opus deep); context per cycle (working-set + retrieval). *(still to detail)*

**B. Tools:**
- 🔑 The retrieval tool set (over which layers/KB); **whether to build the Operational Journal** (6th layer: raw `(command,output)` + RAG).

**C. Outputs:**
- Recommendation shape (why / OPSEC risk / suggested action / confidence / references) + `#advisor-dock` UX (single / stream / attached by `correlation_id`; dismiss / follow-up / accept).
- Interpretation-writing mechanics (auto-write vs propose-and-confirm per field; attribution/reversal in the UI).

**D. Learning loop:**
- Feedback mechanics (Chat + `operator.question/answer`); lesson extraction (context-scoped); storage (extend L1? new type? per-engagement vs cross-engagement scope); read-back (tool / cached block).

**E. Safety / quality:**
- Grounding + scope-safety + anti-hallucination + redaction at the LLM boundary.
- Failure/degraded/cost/observability (`assistant.*` events).

**F. Scope:**
- 🔑 The first buildable slice (thesis-first, not "MVP for demo") + build order.

---

Related memory: `ai-assistant-thesis-first`, `ai-assistant-design-decisions`, `redrobin-project-journal-location` (in the spiedra-vault Claude auto-memory). Code discovery: 2026-07-11 (memory system, LLM+retrieval, bus+TUI+verdict, engagement+wiring maps).
