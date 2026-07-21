# Proactive mode — Task 4 (#25) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the **proactive** trigger mode — the assistant speaks *self-triggered* (watch-your-back), on a **debounced background check** that fires on a **lull** (no beacon output for `X`) OR a **material state-change** (a newly-observed defence), through the **same** `TriggerGate` Task 2 built, rate-limited to ≤ 1 proactive utterance / `N`.

**Architecture:** No new components, no scheduler, no fixed timer. The proactive cadence lives inside the existing `Assistant.run()` loop: the loop already `await`s the next bus event, so a **dynamic `asyncio.wait_for` timeout** (computed from the last beacon beat) makes the lull fire when the queue goes quiet — the debounce IS the queue-get timeout. A material state-change is detected inline on a `working_memory.deltas` and backstopped after the reactive cycle. Both funnel into one `_maybe_proactive()` that reuses the **same** `TriggerGate.should_speak` (novelty + cooldown naturally make proactive a backstop that never crowds reactive) plus a separate proactive rate-limit, and reuses `run_cycle` with `correlation_id=None` and a proactive "watch-your-back" framing. `X` (lull) and `N` (rate-limit) are tunable `Settings`.

**Tech Stack:** Python 3.11+ (`asyncio.TimeoutError is TimeoutError` since 3.11, so `except TimeoutError:` catches `wait_for` timeouts), `asyncio`, native Anthropic Tool Runner (via the Task-2 `AgentLLM`/`run_cycle` seam), `pytest` + `pytest-asyncio`, `uv`, `ruff`, `mypy --strict` (domain+application), `import-linter`.

## Global Constraints

_Every task's requirements implicitly include this section. Values verbatim from the spec (`docs/superpowers/specs/2026-07-11-ai-assistant-design.md` §6.A), `CLAUDE.md`, `CONTRIBUTING.md`, issue #25._

- **The 5 CI gates must pass** (verbatim from `CLAUDE.md`): `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy redrobin/domain redrobin/application` · `uv run lint-imports --config importlinter.cfg` · `uv run pytest tests/ -v --tb=short`. Use `uv run --no-sync ...` if a bare `uv run` fails on a venv lock; `REDROBIN_ALLOW_DEGRADED=1` for boot-gated tests.
- **ruff default rules incl. E402** — all imports at the very top of every file.
- **TDD (red→green)**; **atomic conventional commits, NO AI co-author trailer**.
- **No new scheduler / fixed timer** (spec §6.A, issue #25 out-of-scope) — a debounced background check only. The lull is a **dynamic** `wait_for` timeout, not a periodic tick.
- **No new signal-not-noise gate** — proactive passes the **same** `TriggerGate`; do NOT fork a second gate (issue #25 out-of-scope).
- **`X`, `N` are tunable `Settings`, not hardcoded** (spec §6.A).
- **Never-crash + degraded-None** (spec §6.E): `llm is None` or `not enabled` → no proactive utterances, daemon boots; any exception in the background check / a proactive cycle is logged and swallowed, the loop survives (mirror the reactive path's `try/except` at `assistant.py:82-85`).
- **Observability** (spec §6.E): `assistant:`-prefixed logs distinguishing proactive from reactive (a `reason=` field); no new bus event type — proactive publishes the SAME `advisor.recommendation`.
- **mypy --strict** on `redrobin/application`; layered arch unchanged (this task touches only `application` + `infrastructure/config.py` + `api/lifespan_helpers.py`).
- **Reuse, don't re-architect** — same `TriggerGate`, same `run_cycle`, same `advisor.recommendation`, same `#advisor-dock` (§6.A / §6.F).

## Design Decisions (locked in-plan from the spec — no re-brainstorm)

- **D1 — The lull is a dynamic `wait_for` timeout on the existing loop.** `Assistant.run()` already does `evt = await sub.get()` on an `asyncio.Queue`. Replace it with `await asyncio.wait_for(sub.get(), timeout=self._lull_timeout())`; `_lull_timeout()` = `max(0, lull_sec − (now − last_beat))`, or `None` when proactive is inactive (disabled / degraded / no beat yet). On `TimeoutError` → the queue was quiet for the lull → fire a proactive check. This is the "debounced background check, no fixed timer, no scheduler" the spec mandates — the timeout shrinks as the beat ages and resets when activity arrives.
- **D2 — Reuse the SAME gate with `correlation_id=None`; it makes proactive a backstop for free.** `TriggerGate.should_speak(correlation_id=None, state_signature=sig)` skips the corr-dedup (None) and relies on **novelty** (won't speak if the state is unchanged since the last utterance — reactive OR proactive) + **cooldown** (won't crowd a recent reactive utterance). So proactive only speaks about state the assistant has NOT already addressed. The proactive **rate-limit** (`N`) is a *separate, additional* constraint tracked in the `Assistant` (`_last_proactive`), because `N` ≫ the gate's per-utterance cooldown.
- **D3 — "material state-change" = a `working_memory.deltas` that adds a new `defenses_observed`.** This is the spec's "accumulated OPSEC risk" watch-your-back signal — concrete, high-value, testable, and rarely noisy (a new EDR/AV is a real event). It is checked as a **backstop AFTER the reactive cycle** for that beat: reactive gets first crack; the gate's novelty/cooldown then suppresses the proactive check automatically when reactive already spoke, and lets it through only when reactive stayed silent on a beat carrying a new defence. (Other "high-value finding / phase boundary" signals from §6.A are caught by the lull path scanning the accumulated state; keeping the immediate-fire signal to defences avoids a noisy second gate — a deliberate simplification.)
- **D4 — Proactive framing reuses `run_cycle` via a `proactive: bool` flag.** `run_cycle(..., proactive=True, correlation_id=None)` swaps `build_push_context`'s "The operator ran: X" block for a "no command was just run — scan for an unaddressed OPSEC risk / stalled objective / untaken opportunity" block. Same 5-part output contract, same tools, same Haiku fast path.
- **D5 — Acceptance form.** The cadence/gate/rate-limit/framing logic is deterministic → the 5 CI gates + the new tests ARE the acceptance. **Clock is injected** into `Assistant` so the rate-limit + decision logic test deterministically; the async lull wiring is proven by one fast real-time test (small `lull_sec`). Task 4 DOES add a live-surface behaviour (a card appearing with NO command, on a real idle/defence) → an **operator smoke checkpoint** is added to the runbook, but per the **operator-agreed smoke-deferral decision (2026-07-20, recorded in project memory)** it runs in the **consolidated pre-`dev`-merge smoke session**: batch all live-surface smokes into one operator session before the `dev` merge; deferring *within* the branch is fine, but Task 4 is not merge-to-`dev`-complete until that checkpoint passes (do NOT tick the live-surface DoD box before it runs).

## File Structure

**Modify:**
- `redrobin/application/agents/assistant/agent.py` — add `proactive: bool = False` to `build_push_context` + `run_cycle` (the watch-your-back framing).
- `redrobin/application/agents/assistant/assistant.py` — the proactive cadence: `__init__` gains `clock` / `lull_sec` / `proactive_min_interval_sec`; the run-loop gains the `wait_for` lull debounce + the material-change detection + backstop; a new `_maybe_proactive` + `_lull_timeout`.
- `redrobin/infrastructure/config.py` — `assistant_lull_sec` + `assistant_proactive_min_interval_sec` Settings (+ the `_positive_float` validator).
- `redrobin/api/lifespan_helpers.py` — pass the two new settings into `Assistant(...)`.
- `scripts/smoke/rr-assistant-smoke-runbook.md` — add a `[Task 4 (#25)]` proactive checkpoint (runs in the batched smoke session).

**Create:**
- `tests/unit/agents/assistant/test_assistant_proactive.py` — the cadence/gate/rate-limit/material-change tests (clock-injected deterministic + one fast async lull test).

**Untouched (verify, do not modify):** `triggers.py` (reused as-is — NO second gate), `tools.py`, `journal_service.py`, `parser.py`, `llm.py`, the TUI/dock, the reducer, the `.cna`, `domain/assistant.py`.

---

### Task 4.1: Proactive framing in `agent.py`

Give `run_cycle` a proactive mode that reframes the push-context as a watch-your-back scan instead of command commentary. Isolated + foundational; the cadence (4.2) calls it.

**Files:**
- Modify: `redrobin/application/agents/assistant/agent.py`
- Test: `tests/unit/agents/assistant/test_agent.py`

**Interfaces:**
- Produces: `build_push_context(*, wm_snapshot, l5_summary, trigger_command, wm_max_items=_DEFAULT_WM_MAX_ITEMS, proactive: bool = False) -> str`; `run_cycle(*, ..., proactive: bool = False)` (new kwarg, default False — reactive callers unchanged).

- [ ] **Step 1: Write the failing test** (append to `tests/unit/agents/assistant/test_agent.py`; keep imports at top)

```python
def test_build_push_context_proactive_frames_watch_your_back():
    ctx = build_push_context(
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon done",
        trigger_command="",
        proactive=True,
    )
    assert "Proactive check" in ctx
    assert "no command was just run" in ctx.lower()
    assert "operator ran" not in ctx.lower()  # NOT the reactive framing


def test_build_push_context_reactive_is_unchanged():
    ctx = build_push_context(
        wm_snapshot={}, l5_summary="", trigger_command="whoami", proactive=False
    )
    assert "The operator ran: whoami" in ctx
    assert "Proactive check" not in ctx


@pytest.mark.asyncio
async def test_run_cycle_proactive_sends_watch_your_back_and_null_correlation():
    seen = {}

    class _LLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            seen["user_message"] = user_message
            return (
                '{"suggested_action":"rotate to comsvcs","rationale":"why","opsec_risk":"WARN",'
                '"confidence":"medium","references":["wm"]}'
            )

    rec = await run_cycle(
        llm=_LLM(),
        tools=[],
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon done",
        trigger_command="",
        correlation_id=None,
        proactive=True,
    )
    assert rec is not None
    assert rec.correlation_id is None
    assert "Proactive check" in seen["user_message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_agent.py -k proactive -v`
Expected: FAIL — `build_push_context()`/`run_cycle()` got an unexpected keyword argument `proactive`.

- [ ] **Step 3: Write the implementation** (edit `redrobin/application/agents/assistant/agent.py`)

Change `build_push_context` to branch on `proactive` for the last block:

```python
def build_push_context(
    *,
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
    proactive: bool = False,
) -> str:
    """Assemble the small, token-bounded PUSH working-set: L2 (current working
    memory) + L5 (story so far) + the trigger framing. Reactive framing names the
    command just run; proactive framing (``proactive=True``) asks for a
    watch-your-back scan of the accumulated state (no command was run). The L2
    snapshot is capped to the most-recent ``wm_max_items`` per category
    (context-rot guard, spec §6.B refinement 2). Non-mutating."""
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    wm_json = json.dumps(capped, ensure_ascii=False, sort_keys=True)
    lines = [
        "# Engagement working memory (L2)",
        wm_json,
        "",
        "# Story so far (L5)",
        l5_summary or "(no summary yet)",
        "",
    ]
    if proactive:
        lines += [
            "# Proactive check — no command was just run",
            "Scan the accumulated engagement state for a WATCH-YOUR-BACK concern the "
            "per-command view may have missed: an unaddressed OPSEC risk, a stalled "
            "objective, or an untaken opportunity.",
            "",
            "If there is a material, grounded one, retrieve any tradecraft you need and "
            'return the 5-part JSON. If not, return {"speak": false}.',
        ]
    else:
        lines += [
            "# What just happened",
            f"The operator ran: {trigger_command or '(unknown command)'}",
            "",
            "Given this, is there a material, grounded next-step recommendation? If yes, "
            'retrieve any tradecraft you need and return the 5-part JSON. If not, return {"speak": false}.',
        ]
    return "\n".join(lines)
```

Thread `proactive` through `run_cycle`:

```python
async def run_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    correlation_id: str | None,
    max_tokens: int = 2048,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
    proactive: bool = False,
) -> Recommendation | None:
    """Run ONE agent cycle: assemble -> Tool Runner loop (via AgentLLM) -> parse
    into a Recommendation, or None to stay silent. ``proactive=True`` swaps the
    reactive command framing for the watch-your-back scan (spec §6.A)."""
    raw = await llm.run(
        system=build_system_prompt(),
        user_message=build_push_context(
            wm_snapshot=wm_snapshot,
            l5_summary=l5_summary,
            trigger_command=trigger_command,
            wm_max_items=wm_max_items,
            proactive=proactive,
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    return parse_recommendation(raw, correlation_id=correlation_id)
```

Leave the `run_cycle` no-retry comment intact.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_agent.py -v`
Expected: PASS (existing + the 3 new).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/agent.py tests/unit/agents/assistant/test_agent.py
git commit -m "feat(assistant): proactive framing for run_cycle (watch-your-back push-context)"
```

---

### Task 4.2: The proactive cadence in `assistant.py`

The core. Add the lull debounce, the material-change backstop, and `_maybe_proactive` (same gate + proactive rate-limit) to the run loop. Clock-injected.

**Files:**
- Modify: `redrobin/application/agents/assistant/assistant.py`
- Test: create `tests/unit/agents/assistant/test_assistant_proactive.py`

**Interfaces:**
- Consumes: `run_cycle(..., proactive=True)` (4.1); `TriggerGate.should_speak`/`record_spoken`; `state_signature`.
- Produces: `Assistant.__init__` gains `clock: Callable[[], float] = time.monotonic`, `lull_sec: float = 300.0`, `proactive_min_interval_sec: float = 600.0`; new `async def _maybe_proactive(self, reason: str) -> None`, `def _lull_timeout(self) -> float | None`.

- [ ] **Step 1: Write the failing tests** (create `tests/unit/agents/assistant/test_assistant_proactive.py`, imports at top)

```python
from __future__ import annotations

import asyncio
import time

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus

_REC_JSON = (
    '{"suggested_action":"rotate to comsvcs","rationale":"CrowdStrike is loud",'
    '"opsec_risk":"WARN","confidence":"medium","references":["wm"]}'
)
_UNSET = object()  # sentinel so llm=None yields Assistant(llm=None), not the default fake


class _FramingLLM:
    """Speaks (returns a rec) ONLY for the proactive framing; silent otherwise —
    lets a test isolate a proactive utterance from a reactive one."""

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        if "Proactive check" in user_message:
            return _REC_JSON
        return '{"speak": false}'


def _assistant(bus, clock, *, lull_sec=300.0, proactive_min_interval_sec=600.0, llm=_UNSET):
    return Assistant(
        bus=bus,
        llm=_FramingLLM() if llm is _UNSET else llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=clock),
        summary_reader=lambda: "recon done",
        enabled=True,
        clock=clock,
        lull_sec=lull_sec,
        proactive_min_interval_sec=proactive_min_interval_sec,
    )


@pytest.mark.asyncio
async def test_maybe_proactive_publishes_when_novel_and_rate_ok():
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0])
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    evt = sub.get_nowait()
    assert evt["type"] == "advisor.recommendation"
    assert evt["payload"]["correlation_id"] is None


@pytest.mark.asyncio
async def test_proactive_rate_limited():
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0], proactive_min_interval_sec=600.0)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("first")
    assert sub.get_nowait()["type"] == "advisor.recommendation"
    # a change in state (novel) but within N -> still suppressed by the rate-limit
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike", "Defender"]}
    t[0] = 1000.0 + 599.0
    await a._maybe_proactive("second")
    assert sub.empty()
    # past N -> allowed again
    t[0] = 1000.0 + 601.0
    await a._maybe_proactive("third")
    assert sub.get_nowait()["type"] == "advisor.recommendation"


@pytest.mark.asyncio
async def test_proactive_silent_on_unchanged_state_novelty_gate():
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0])
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("first")
    assert sub.get_nowait()["type"] == "advisor.recommendation"
    # unchanged state, well past the rate-limit -> the SAME gate's novelty stays silent
    t[0] = 1_000_000.0
    await a._maybe_proactive("again")
    assert sub.empty()


@pytest.mark.asyncio
async def test_proactive_degraded_no_llm_is_silent():
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, lambda: 0.0, llm=None)  # Assistant(llm=None) — degraded
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    assert sub.empty()


@pytest.mark.asyncio
async def test_material_change_backstops_when_reactive_silent():
    # A new defence lands (material). The FramingLLM is silent for reactive
    # (enrichment) framing but speaks for proactive -> the backstop fires.
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0])
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())  # on_started assigned BEFORE create_task
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "working_memory.deltas", "payload": {"defenses_observed": ["CrowdStrike"]}})
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}})
    # drain: expect EXACTLY ONE card — the proactive backstop (reactive stayed silent),
    # never both for the same state.
    recs = []
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "advisor.recommendation":
            recs.append(evt)
    assert len(recs) == 1
    assert recs[0]["payload"]["correlation_id"] is None
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_lull_fires_after_idle():
    # Small real lull so the wait_for timeout elapses fast; after a beat with no
    # further events, the lull proactive fires.
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, time.monotonic, lull_sec=0.05, proactive_min_interval_sec=0.0)
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())  # on_started assigned BEFORE create_task
    await asyncio.wait_for(started.wait(), 1.0)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "ls", "beacon_id": "b1"}})
    rec = None
    for _ in range(50):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.1)
        except TimeoutError:
            continue
        if evt.get("type") == "advisor.recommendation":
            rec = evt
            break
    assert rec is not None and rec["payload"]["correlation_id"] is None
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_proactive_survives_cycle_exception():
    # A raising LLM inside a proactive cycle is logged and swallowed by
    # _safe_proactive; the run loop survives (never-crash, spec §6.E) — the
    # proactive counterpart of test_assistant_survives_cycle_exception.
    class _BoomLLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            if "Proactive check" in user_message:
                raise RuntimeError("llm exploded")  # only the proactive cycle raises
            return '{"speak": false}'  # reactive stays silent

    bus = EventBus()
    a = _assistant(bus, lambda: 1000.0, llm=_BoomLLM())
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "working_memory.deltas", "payload": {"defenses_observed": ["CrowdStrike"]}})
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}})
    await asyncio.sleep(0.05)
    assert not task.done()  # the proactive exception did NOT crash the loop
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_assistant_proactive.py -v`
Expected: FAIL — `Assistant.__init__() got an unexpected keyword argument 'clock'` / no `_maybe_proactive`.

- [ ] **Step 3: Write the implementation** (edit `redrobin/application/agents/assistant/assistant.py`)

Add `import asyncio` and `import time` to the top stdlib import block (ruff isort order — the block currently is just `import logging`, so it becomes `asyncio`, `logging`, `time`):

```python
import asyncio
import logging
import time
```

Extend `__init__` (add the three params + state fields):

```python
    def __init__(
        self,
        *,
        bus: EventBus,
        llm: AgentLLM | None,
        tools: list[AgentTool],
        trigger: TriggerGate,
        summary_reader: Callable[[], str],
        enabled: bool = True,
        max_tokens: int = 2048,
        wm_max_items: int = 20,
        clock: Callable[[], float] = time.monotonic,
        lull_sec: float = 300.0,
        proactive_min_interval_sec: float = 600.0,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._tools = tools
        self._trigger = trigger
        self._summary_reader = summary_reader
        self._enabled = enabled
        self._max_tokens = max_tokens
        self._wm_max_items = wm_max_items
        self._wm_snapshot: dict[str, Any] = {}
        self.on_started: Callable[[], None] | None = None
        # Proactive cadence (spec §6.A): a debounced background check, no scheduler.
        self._clock = clock
        self._lull_sec = lull_sec
        self._proactive_min_interval = proactive_min_interval_sec
        self._last_beat: float | None = None  # last beacon beat OR lull-check; drives the debounce
        self._last_proactive: float | None = None  # proactive rate-limit (N)
        self._material_pending = False  # a new defence awaits the post-reactive backstop
```

Replace the `run()` loop body's event-wait + the two handled branches:

```python
    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("assistant on_started callback raised")
        try:
            while True:
                timeout = self._lull_timeout()
                if timeout == 0.0:
                    # Lull already due at loop-top — fire without a zero wait_for.
                    self._last_beat = self._clock()
                    await self._safe_proactive("lull")
                    continue
                try:
                    evt = (
                        await asyncio.wait_for(sub.get(), timeout)
                        if timeout is not None
                        else await sub.get()
                    )
                except TimeoutError:
                    self._last_beat = self._clock()  # reset the debounce (no busy-loop)
                    await self._safe_proactive("lull")
                    continue
                if evt.get("_type") == "_close":
                    return
                etype = evt.get("type")
                if etype == "working_memory_snapshot":
                    payload = evt.get("payload")
                    if isinstance(payload, dict):
                        self._wm_snapshot = payload  # authoritative replace
                    continue
                if etype == "working_memory.deltas":
                    payload = evt.get("payload")
                    if isinstance(payload, dict):
                        self._wm_snapshot = apply_deltas_to_snapshot(self._wm_snapshot, payload)
                        if payload.get("defenses_observed"):
                            self._material_pending = True  # a new defence — backstop after reactive
                    continue
                if etype != "enrichment.completed":
                    continue
                self._last_beat = self._clock()  # a beacon beat resets the lull debounce
                try:
                    await self._on_enrichment(evt.get("payload") or {})
                except Exception:
                    logger.exception("assistant failed to process enrichment.completed")
                if self._material_pending:
                    self._material_pending = False
                    await self._safe_proactive("material_change")
        finally:
            await self._bus.unsubscribe(sub)
```

Add the three new methods (leave `_on_enrichment` unchanged):

```python
    def _lull_timeout(self) -> float | None:
        """Seconds until the next proactive lull check, or None when proactive is
        inactive (disabled / degraded / no beacon beat yet). DYNAMIC — computed
        from the last beat, NOT a fixed periodic timer (spec §6.A: no scheduler)."""
        if self._llm is None or not self._enabled or self._last_beat is None:
            return None
        return max(0.0, self._lull_sec - (self._clock() - self._last_beat))

    async def _safe_proactive(self, reason: str) -> None:
        """Never-crash wrapper (spec §6.E): a proactive cycle's failure is logged,
        the run loop survives."""
        try:
            await self._maybe_proactive(reason)
        except Exception:
            logger.exception("assistant: proactive check failed reason=%s", reason)

    async def _maybe_proactive(self, reason: str) -> None:
        """One proactive (self-triggered) check. Reuses the SAME signal-not-noise
        gate as reactive (novelty makes it a backstop that never crowds reactive;
        cooldown shared) plus a separate proactive rate-limit (N). Publishes the
        SAME advisor.recommendation with correlation_id=None."""
        if self._llm is None or not self._enabled:
            return
        if not self._wm_snapshot:
            return  # nothing to reason about yet — don't burn a cycle on empty state
        now = self._clock()
        if self._last_proactive is not None and (now - self._last_proactive) < self._proactive_min_interval:
            return  # proactive rate-limit: <= 1 proactive utterance / N
        sig = state_signature(self._wm_snapshot)
        if not self._trigger.should_speak(correlation_id=None, state_signature=sig):
            return  # SAME gate — novelty/cooldown backstop
        try:
            l5_summary = self._summary_reader()
        except Exception:
            logger.exception("assistant: summary_reader failed; proceeding with empty L5")
            l5_summary = ""
        rec = await run_cycle(
            llm=self._llm,
            tools=self._tools,
            wm_snapshot=self._wm_snapshot,
            l5_summary=l5_summary,
            trigger_command="",
            correlation_id=None,
            max_tokens=self._max_tokens,
            wm_max_items=self._wm_max_items,
            proactive=True,
        )
        if rec is None:
            return  # signal-not-noise: nothing material
        self._trigger.record_spoken(correlation_id=None, state_signature=sig)
        self._last_proactive = now
        await self._bus.publish({"type": "advisor.recommendation", "payload": rec.to_payload()})
        logger.info(
            "assistant: published proactive advisor.recommendation reason=%s risk=%s",
            reason,
            rec.opsec_risk.value,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_assistant_proactive.py tests/integration/test_assistant.py -v`
Expected: PASS (the new proactive tests + the existing reactive tests still green — the reactive path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/assistant.py tests/unit/agents/assistant/test_assistant_proactive.py
git commit -m "feat(assistant): proactive mode — lull debounce + material-change backstop through the same gate"
```

---

### Task 4.3: Tunable Settings + lifespan wiring

Expose `X` (lull) and `N` (rate-limit) as `Settings` and thread them into the daemon's `Assistant`.

**Files:**
- Modify: `redrobin/infrastructure/config.py`, `redrobin/api/lifespan_helpers.py`
- Test: `tests/integration/test_assistant_lifespan.py`

**Interfaces:**
- Produces: `Settings.assistant_lull_sec: float` (default 300.0, `REDROBIN_ASSISTANT_LULL_SEC`), `Settings.assistant_proactive_min_interval_sec: float` (default 600.0, `REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC`).

- [ ] **Step 1: Write the failing test** (append to `tests/integration/test_assistant_lifespan.py`, imports at top)

```python
def test_settings_expose_proactive_knobs(monkeypatch):
    monkeypatch.delenv("REDROBIN_ASSISTANT_LULL_SEC", raising=False)
    monkeypatch.delenv("REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC", raising=False)
    from redrobin.application.config import Settings

    s = Settings()
    assert s.assistant_lull_sec == 300.0
    assert s.assistant_proactive_min_interval_sec == 600.0


def test_settings_proactive_knobs_override(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_LULL_SEC", "45")
    monkeypatch.setenv("REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC", "90")
    from redrobin.application.config import Settings

    s = Settings()
    assert s.assistant_lull_sec == 45.0
    assert s.assistant_proactive_min_interval_sec == 90.0


def test_settings_proactive_knobs_reject_non_positive(monkeypatch):
    import pytest as _pytest

    monkeypatch.setenv("REDROBIN_ASSISTANT_LULL_SEC", "0")
    from redrobin.application.config import Settings

    with _pytest.raises(Exception) as exc:
        Settings()
    assert "> 0" in str(exc.value) or "assistant_lull_sec" in str(exc.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/integration/test_assistant_lifespan.py -k proactive -v`
Expected: FAIL — `Settings` has no attribute `assistant_lull_sec`.

- [ ] **Step 3: Write the implementation**

In `redrobin/infrastructure/config.py`, after `assistant_wm_max_items` (line ~96) add:

```python
    # Proactive mode (Task 4 §6.A): X = lull before a self-triggered check;
    # N = min interval between proactive utterances (rate-limit). Tunable, not hardcoded.
    assistant_lull_sec: float = Field(default=300.0, alias="REDROBIN_ASSISTANT_LULL_SEC")
    assistant_proactive_min_interval_sec: float = Field(
        default=600.0, alias="REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC"
    )
```

Add both to the existing `_positive_float` validator (currently `@field_validator("promoter_interval_sec", "l5_interval_sec", "assistant_cooldown_sec")`):

```python
    @field_validator(
        "promoter_interval_sec",
        "l5_interval_sec",
        "assistant_cooldown_sec",
        "assistant_lull_sec",
        "assistant_proactive_min_interval_sec",
    )
    @classmethod
    def _positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v
```

In `redrobin/api/lifespan_helpers.py` `start_assistant`, pass the two settings into `Assistant(...)`:

```python
    assistant = Assistant(
        bus=app.state.event_bus,
        llm=build_agent_llm(model=settings.assistant_model),
        tools=build_tool_registry(kb, journal=journal),
        trigger=TriggerGate(cooldown_sec=settings.assistant_cooldown_sec),
        summary_reader=_read_summary,
        enabled=settings.assistant_enabled,
        max_tokens=settings.assistant_max_tokens,
        wm_max_items=settings.assistant_wm_max_items,
        lull_sec=settings.assistant_lull_sec,
        proactive_min_interval_sec=settings.assistant_proactive_min_interval_sec,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_assistant_lifespan.py -v`
Expected: PASS (the 3 new + the existing lifespan tests).

- [ ] **Step 5: Commit**

```bash
git add redrobin/infrastructure/config.py redrobin/api/lifespan_helpers.py tests/integration/test_assistant_lifespan.py
git commit -m "feat(assistant): tunable proactive lull + rate-limit settings, threaded into start_assistant"
```

---

### Task 4.4: Operator-smoke checkpoint (runbook)

Add the proactive live-surface checkpoint so it's ready for the consolidated pre-`dev`-merge smoke session (per the 2026-07-20 deferral decision). Docs only — no code, no gate impact.

**Files:**
- Modify: `scripts/smoke/rr-assistant-smoke-runbook.md`

- [ ] **Step 1: Add the checkpoint** — append a `[Task 4 (#25)] Proactive` section to the runbook, mirroring the existing checkpoint style:

```markdown
## [Task 4 (#25)] Proactive mode

Prereq: assistant enabled (`REDROBIN_ASSISTANT_ENABLED=1`) with an ANTHROPIC key; set a short lull for the test (`REDROBIN_ASSISTANT_LULL_SEC=60`, `REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC=60`).

- **H — Lull:** run one benign command so the assistant has state, then leave the beacon idle (no new commands) for > the lull. EXPECT: a proactive `advisor.recommendation` card appears in `#advisor-dock` with NO command having been run, carrying all 5 parts. (If the state is unchanged since the last card, the novelty gate correctly stays silent — advance state first.)
- **I — Material change:** run a command whose output reveals a new defence (e.g. an AV/EDR banner). EXPECT: either the reactive card (if reactive spoke) or, if reactive stayed silent, a proactive watch-your-back card about the new defence — never both for the same state.
- **J — Rate-limit / signal-not-noise:** after a proactive card, confirm no second proactive card within `N` even across further idle. Advisory-only throughout (nothing dispatched to the beacon).
```

- [ ] **Step 2: Commit**

```bash
git add scripts/smoke/rr-assistant-smoke-runbook.md
git commit -m "docs(smoke): add proactive-mode operator checkpoints (Task 4) for the batched smoke"
```

- [ ] **Step 3: Run the 5 CI gates (Task-4 acceptance)**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q --tb=short
```

Expected: all green; import-linter 4 KEPT; pytest = prior baseline + the new proactive tests, pristine.

---

## Acceptance / Definition of Done (Task 4 / #25)

- [ ] **5 CI gates green.**
- [ ] **TDD red→green** for: proactive framing (4.1); lull fire, material-change backstop, silent-on-noise, rate-limit, degraded (4.2); settings + validation (4.3).
- [ ] **Fires on a lull AND on a material state-change**; **silent on noise** (unchanged state → the same gate's novelty); **rate-limited** ≤ 1 proactive / `N`; `X`/`N` from `Settings`; **clock-injected** deterministic tests (4.2/4.3).
- [ ] **Never-crash + degraded-None** — no key/disabled → no proactive, daemon boots; a proactive exception is logged (`_safe_proactive`), the loop survives.
- [ ] **Observability** — `assistant: published proactive advisor.recommendation reason=…` logs.
- [ ] **Plugs into the seam** — SAME `TriggerGate` + `run_cycle` + `advisor.recommendation`; **no scheduler, no fixed timer** (dynamic `wait_for` debounce); NO second gate.
- [ ] **Acceptance form (D5)** — deterministic logic → CI gates + tests ARE the acceptance; the live-surface proactive card → an operator-smoke checkpoint added to the runbook, run in the **batched pre-`dev`-merge session**.
- [ ] **Atomic conventional commits, no AI co-author trailer.**
- [ ] **Pillars** — ① a new trigger through the existing gate + settings, no new architecture; ② debounced-not-scheduled + tunable knobs, cost-safe; ③ proactive cards carry the same 5-part WHY.

## Documented limitations (in-scope, deferred by design)

- **Material signal = new `defenses_observed` only** (D3). Two deliberate exclusions: (a) `findings` (also an enricher category) is intentionally **lull-only, not immediate** — findings are higher-volume/noisier than a defence, so immediate-firing on every finding would risk exactly the noise the gate exists to prevent; the lull scan still surfaces an accumulated high-value finding without per-finding chatter. (b) A "phase boundary" immediate trigger is **not buildable at Task 4** — `current_phase`/`objectives` state doesn't exist in working memory until the Task 6 interpretation-writer (spec §6.C). Broadening the immediate-fire set is a later refinement.
- **Lull debounce granularity** — a proactive lull fires at most once per `lull_sec` of quiet, further bounded by the rate-limit `N` and the novelty gate; there is no sub-lull polling (by design — no scheduler).
- **One consolidated smoke** — the proactive live-surface checkpoints run in the batched pre-merge operator session, not per-task (2026-07-20 deferral decision).

## Self-Review (author checklist — completed)

1. **Spec coverage (§6.A):** proactive self-triggered → 4.2; debounced check, no scheduler/timer → D1/`_lull_timeout`; lull OR material change → 4.2; same signal-not-noise gate → D2 (`should_speak(None,…)`); rate-limit ≤1/N → 4.2; X/N tunable Settings → 4.3; watch-your-back framing → 4.1/D4; two-speeds (Haiku fast path) → reuses `run_cycle` (Haiku). No gaps.
2. **Placeholder scan:** every step has runnable code or an exact command; no TBD/"handle edge cases".
3. **Type consistency:** `build_push_context(..., proactive: bool=False)` + `run_cycle(..., proactive: bool=False)` (reactive callers unchanged, default False); `Assistant.__init__(..., clock, lull_sec, proactive_min_interval_sec)`; `_lull_timeout() -> float | None`; `_maybe_proactive(reason: str) -> None`. Settings floats validated `> 0`. Lifespan passes both settings.
