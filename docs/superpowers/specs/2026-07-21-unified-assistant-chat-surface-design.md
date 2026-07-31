# Unified Assistant Chat Surface — Design Spec

**Date:** 2026-07-21 · **Branch:** `feat-ai-assistant` · **Status:** draft (open decisions pending operator)
**Follows:** the AI-DLC — this is the brainstorming-phase spec; next is `writing-plans` → `auditing-plans` → `subagent-driven-development` → operator smoke → finishing.

---

## §1. Context & Problem

The AI assistant's output is currently split across **two** TUI surfaces:

- **ADVISOR dock** (bottom strip, `#advisor-dock`) — reactive + proactive `advisor.recommendation` cards (the assistant speaking unprompted).
- **Assistant Chat pane** (center, `#events` → `ChatPane`) — conversational answers (`operator.answer`) + interpretation confirms.

Both are produced by the **same brain**: one `Assistant` consumer, one `AgentLLM` (Anthropic Tool Runner), the same tools (`kb_retrieve`/`search_journal`/`get_lessons`) and the same push-context (WM + L5). They differ only by **cycle**: `run_cycle` (reactive/proactive → recommendation), `run_answer_cycle` (conversational → answer), `run_interpretation_cycle` (→ confirm). The split is **path-dependent**, not architectural: the dock was the pre-wired seam the first assistant slice (Task 2 #23) targeted; the Chat pane was the last piece (#28). It fragments operator attention and misrepresents the "one brain" design — and it is already inconsistent (recommendations → dock, but confirms → chat).

**Operator decision (2026-07-21):** unify everything into the Chat pane; remove the dock.

## §2. Goal

Make the **Assistant Chat pane the single surface** for all assistant-authored output — reactive + proactive recommendations render as messages in the same stream as answers and confirms — and **remove the `#advisor-dock`**. No change to the assistant's reasoning, cycles, events, or model; this is a **presentation-layer** change only.

## §3. Pillars

- **Clarity (UX) — the driver.** One brain → one voice → one surface. The operator watches one place; provenance (the assistant spoke unprompted vs answered you) is legible; the UI matches the architecture instead of a build accident.
- **Maintainability.** A single render path for assistant output; the `@REGISTRY.register("<type>")` frame pattern is preserved (a handler per event type, no dispatch-loop edits); removes the dock's bespoke reveal/pin/`display` special-casing and one whole pane from `compose`/`theme.tcss`.
- **Scalability.** The existing signal-not-noise gate (dedup/cooldown/novelty) already bounds how often recommendations arrive, so interleaving them with Q&A does not flood the stream. **Gap found in review:** `#chat-log` is an unbounded `RichLog` today (no `max_lines`) — over a long engagement it grows without limit. This change sets a generous `max_lines` cap on `#chat-log` (in-scope hardening; also bounds answers/confirms, which are unbounded today) so the single stream stays memory-bounded.

## §4. Design

### §4.1 Rendering (reactive + proactive → chat)
- A chat frame handler `_on_recommendation(app, payload)` appends the recommendation to `#chat-log` (the `RichLog`), guarded by the `NoMatches` pre-mount pattern (never-crash), mirroring the existing `_on_answer`/`_on_question`.
- **Adapt + move `format_recommendation`** into `chat.py` (beside `format_answer`/`format_confirm`/`format_operator_echo` — all pure chat renderers live together) and **delete `advisor.py`**. The 5 mandatory parts are preserved (the thesis: advice *with* the WHY, never hidden), but the renderer's contract **changes**: its header line goes from `ADVISOR` to `Red Robin · <tag>`, and it reads `payload["correlation_id"]` to pick the tag (see below). New signature stays `format_recommendation(payload: dict) -> Text`.
- **Provenance tag (OD-1 (c), resolved).** One voice (`Red Robin`) + a muted one-word tag naming the act, driven by `payload["correlation_id"]` — **already distinguishes reactive from proactive for free**: reactive carries `correlation_id="<beacon>:<command>"`, proactive carries `correlation_id=None` (no backend change → stays presentation-only):
  - reactive → `Red Robin · advisory`  ·  proactive → `Red Robin · heads-up`  ·  confirm → `Red Robin · confirm  … [/y /n]`  ·  answer → plain `Red Robin`.
  - Styling with the existing tokens only: speaker in accent (`#58a6ff`), tag in muted (`#8b949e`), OPSEC risk in its severity color. No emoji/badges. Keep the `(nothing dispatched)` reassurance on advisories.
  - Timestamps: **off** (keep the clean Claude-Code-chat feel, OD-3). `correlation_id` still holds the command if a `▸ on <cmd>` variant is ever wanted.
- Auto-scroll to newest (RichLog default); full history via scrollback. **No pinned "latest" card** — the accepted tradeoff of unification (OD-3: "like a Claude Code chat").

### §4.2 Layout
- Remove `yield Container(id="advisor-dock")` from `app.py:compose`.
- Remove `#advisor-dock` + `#advisor-card` rules from `theme.tcss`. The three-pane `Horizontal(#main)` + footer reclaim the dock's height.

### §4.3 Registry
- Register `advisor.recommendation` via the chat binder (`register_chat` gains the third handler, beside `operator.answer`/`operator.question`); **delete `advisor.py`** and its `register_advisor` binder + the `register_advisor(REGISTRY)` call in `app.py`.

### §4.4 Confirm label (resolved — was a spec contradiction)
- OD-1 unifies the voice, so `format_confirm` **changes** from `CONFIRM  … [reply /y or /n]` to `Red Robin · confirm  … [/y /n]` (label only). The confirm **behavior + routing are unchanged** — `next_operator_message`, the `/y /n` token match, `_pending`, and `resolve_confirm` are all untouched; only the rendered header changes. (The earlier "confirms unchanged" was wrong — corrected here.)

### §4.5 Unchanged (explicitly)
- The assistant's cycles/events/model — same brain, same `advisor.recommendation`/`operator.answer`/`operator.question` bus events; false-commit routing untouched.
- Answers render exactly as today (`Red Robin  <prose>`).
- The topbar verdict counts (`BLOCK/WARN/PASS/ESCALATE`) — that is the **gate** (Layer A/B), not the assistant; untouched.
- Enricher, gate, Sessions pane, Working-Memory pane — untouched.

## §5. Resolved Decisions (operator, 2026-07-21)

- **OD-1 — provenance labeling → (c), on UX best practice.** One voice + a muted one-word act tag: reactive `advisory` / proactive `heads-up` / `confirm` / plain `Red Robin` for answers. Rationale: marking solicited vs unsolicited is the #1 trust signal (visibility of system status); a muted tag + the card's own structure carry it without badge noise (aesthetic-minimalist). Distinguishing reactive vs proactive is **free** — `correlation_id` presence already encodes it — so it stays presentation-only. Rendering detail in §4.1.
- **OD-2 — verbosity → (a): full 5-part always.** The WHY is the thesis; a compact form that hides it defeats the point.
- **OD-3 — affordance → (a): pure scrollback, "like a Claude Code chat."** No pinned card, no topbar/WM echo. Clean text-first stream, auto-scroll to newest, history by scrollback.

## §6. Definition of Done

- Reactive **and** proactive `advisor.recommendation` frames render in the Chat pane as 5-part messages, with the correct provenance tag: reactive → `Red Robin · advisory`, proactive → `Red Robin · heads-up` (driven by `correlation_id` presence). Pilot test drives one of each → asserts both in `#chat-log` with the right tag.
- Confirms render as `Red Robin · confirm  … [/y /n]`; answers as `Red Robin  <prose>` (unchanged). The `/y /n` routing + `resolve_confirm` false-commit behavior is untouched (existing routing tests stay green).
- `#advisor-dock` fully removed — from `compose`, `theme.tcss`, `app.py`'s `register_advisor(REGISTRY)` call — and **`advisor.py` deleted**, with **no dangling reference** in production code or tests.
- `#chat-log` gains a `max_lines` cap (bounded memory).
- Topbar verdict counts unchanged.
- The 5 CI gates green; TUI tests updated: `test_advisor.py`'s `format_recommendation` tests move into `test_chat.py` (+ the new tag logic + the `_on_recommendation` handler test); `test_app_layout.py` (no dock in the tree); `test_frames.py` (advisor registration gone from the registry expectations).
- Smoke runbook updated: every checkpoint/line referencing `#advisor-dock` or "the card" — **A, B, D, F, G, H and the header line** (exact set re-confirmed during `auditing-plans`) — reworded to the Chat pane; the "reveal-on-first / the dock was hidden" language removed.
- **Live acceptance:** operator re-smoke of the unified surface — the former dock checkpoints re-run against the chat stream (this is a live-surface change; CI cannot exercise rendering + the WS round-trip).

## §7. Test Strategy

- **Unit (`test_chat.py`):** `format_recommendation` renders the 5 parts **and** the right tag — `correlation_id="b:cmd"` → `Red Robin · advisory`, `correlation_id=None` → `Red Robin · heads-up`; `format_confirm` renders `Red Robin · confirm  … [/y /n]`; the `_on_recommendation` handler appends to the log and is `NoMatches`-guarded (skips nothing pre-mount).
- **Pilot:** mount the app, publish a reactive + a proactive `advisor.recommendation` → assert both render in `#chat-log` with the correct tag; assert **no** `#advisor-dock` in the tree; assert a rec then an answer appear in arrival order; assert a rec arriving does not disturb the `#chat-input` value/focus.
- **Regression:** the `/y /n` confirm routing + `resolve_confirm` unchanged; topbar counts still update; the `c` focus-chat binding + tab order unaffected.

## §8. Non-Goals

- No change to the assistant's reasoning, cycles, events, prompts, or model (same brain).
- No new throttling / signal-not-noise logic — rely on the existing gate.
- No change to enricher, the OPSEC gate, the topbar, the Sessions or Working-Memory panes.
- No `.cna` / adapter change (out of scope; core is C2-agnostic).

## §9. Risks & Blast Radius

- Removing the dock breaks `test_advisor.py`, `test_app_layout.py`, `test_frames.py` and the runbook checkpoints — all enumerated in §6, fixed atomically in the plan.
- A recommendation arriving mid-typing must not disturb the `Input` focus/value — `RichLog.write` appends to the log only; verify in the Pilot test.
- Interleaving unprompted recs with the operator's Q&A could read as noisy — mitigated by the existing gate; watched in the smoke. OD-1 labeling reduces this.

## §10. Acceptance path (methodology)

`writing-plans` → `auditing-plans` (verify blast radius + the Textual API for the removed dock) → `subagent-driven-development` (Opus, TDD, per-task review + whole-change review) → **operator smoke** (re-run the former dock checkpoints against the chat) → finishing (commit atomically, no AI co-author trailer; update the runbook + memory).
