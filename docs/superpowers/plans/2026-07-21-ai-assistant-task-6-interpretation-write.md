# Interpretation-write + propose-and-confirm — Task 6 (#27) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the assistant its SECOND output — it **writes interpretation** (`current_phase` / objective `status`+`progress_pct` / `kill_chain_status`) — in two tiers by stakes: **auto-write** soft/reversible reads, and **propose-and-confirm** (binary, async, NON-BLOCKING) for close/commit changes. It NEVER writes working memory directly — it publishes `engagement.milestone` and the reducer writes it (single-writer, like the enricher).

**Scope:** The CI-testable BACKEND. The confirm-affordance **visual** (rendering a pending proposal + the operator clicking yes/no) is UI, deferred (spec §10) — it rides the `operator.question`/`operator.answer` surface, which is the Chat-pane follow-up **#28**. So this task delivers the mechanics + the plain path; the operator resolves a pending confirm over the (bus-level, tested-here) `operator.answer` channel, with the visual arriving via #28.

**Architecture:** `EngagementMilestoneEvent{phase, milestone_id, status, progress_pct}` and its schema already exist; the reducer's `_dispatch` (working_memory_reducer.py:111) has no case for it. Task 6 adds: (a) the reducer dispatch + a `WorkingMemoryRepo.apply_milestone` (load→mutate→save, single-writer), including a persisted `pending_milestones` list for confirm continuity; (b) an interpretation cycle in the assistant (`run_interpretation_cycle` → `parse_interpretation`, never-raises) that auto-writes soft reads or proposes close/commit changes; (c) the async confirm resolution — the assistant consumes `operator.answer`, matches it to a `pending_milestone` it reads back from the (persisted) WM snapshot (NOT a live session), and commits or drops. No re-architecture — reuses `engagement.milestone`, `operator.question`/`operator.answer`, and the reducer.

**Tech Stack:** Python 3.11+, the existing bus/reducer/repo, native Anthropic Tool Runner (Task-2 `AgentLLM` seam), `pytest` + `pytest-asyncio`, `uv`, `ruff`, `mypy --strict`, `import-linter`.

## Global Constraints

_Verbatim from the spec (§6.C, §1, §4, §6.E), `CLAUDE.md`, `CONTRIBUTING.md`, issue #27._

- **The 5 CI gates must pass** (`uv run --no-sync ...` if a bare `uv run` fails on a venv lock; `REDROBIN_ALLOW_DEGRADED=1` for boot-gated tests): `ruff check .` · `ruff format --check .` · `mypy redrobin/domain redrobin/application` · `lint-imports --config importlinter.cfg` · `pytest tests/ -v --tb=short`.
- **ruff default rules incl. E402** — all imports at the top. **TDD (red→green)**; **atomic conventional commits, NO AI co-author trailer**.
- **The assistant NEVER writes working memory directly** (spec §6.C/§4, issue #27 out-of-scope): it publishes `engagement.milestone`; the reducer stays the single writer.
- **The confirm is binary, async, NON-BLOCKING** (spec §6.C/§1, issue #27 out-of-scope): the assistant proposes a *pending* change and ends — it never freezes waiting, never holds the beacon. The operator resolves it whenever, in a later cycle reconstructed from memory.
- **Continuity lives in the memory layers, NOT a live agent session** (spec §6.C): the pending proposal is persisted in WM (`pending_milestones`) and read back on confirm — the assistant holds NO in-RAM pending state that a restart would lose.
- **Never-crash + degraded-None** (spec §6.E): no key → no interpretation writes, daemon boots; an exception in the interpretation cycle / the confirm resolution is logged and swallowed, the daemon survives; the parser never raises.
- **Observability**: interpretation writes flow via `engagement.milestone`; `assistant:`-prefixed logs (auto-write, proposed, confirmed, dropped).
- **Plug into the seams, no re-architecture** (§6.F): `engagement.milestone` → reducer (single-writer); `operator.question`/`operator.answer` for the confirm. No new gate, no new scheduler.
- **mypy --strict** on `redrobin/domain` + `redrobin/application`.

## Design Decisions (locked in-plan from the spec — no re-brainstorm)

- **D1 — A DEDICATED interpretation cycle, separate from the recommendation cycle.** On `enrichment.completed`, after the reactive path, the assistant runs `run_interpretation_cycle` (a focused Haiku prompt: "given the state, has the phase/objective/kill-chain materially advanced? return an interpretation or null"). Kept SEPARATE (not folded into `run_cycle`'s output) to avoid churning the well-tested Task-2/4 reactive/proactive path + their return contract. Cost = one extra Haiku call per enriched command; bounded, and the assistant is opt-in (disabled by default). *(A future refinement could fold it into the reactive cycle to save the call; documented, not now.)*
- **D2 — Two tiers, keyed by a model-emitted `needs_confirm` flag.** `parse_interpretation` returns `Interpretation{phase, milestone_id, status, progress_pct, needs_confirm, prompt}`. `needs_confirm=False` → **auto-write** (soft/reversible: a phase nudge / progress bump). `needs_confirm=True` → **propose-and-confirm** (close/commit: objective→COMPLETE, kill-chain goal). The model classifies the stakes (spec §6.C names the examples); the prompt instructs it to set `needs_confirm=True` only for changes that CLOSE/COMMIT.
- **D3 — Confirm continuity via a persisted `WorkingMemory.pending_milestones`.** A close/commit proposal publishes `engagement.milestone` with `pending: True` (an INTERNAL bus-payload field — the wire schema is `additionalProperties:false`, but the assistant publishes to the bus directly, NOT through `/v1/events`; this matches the codebase's precedent of internal bus payloads carrying fields beyond the wire event, per `wire-protocol.md`). The reducer records it in `pending_milestones` (does NOT apply it). It ALSO publishes `operator.question` (the ask). On the operator's `operator.answer`, the assistant reads the matching `pending_milestone` from its cached WM snapshot (the memory layer — survives a restart because it's persisted in `working_memory.json`) and commits (publish `pending:False`) or drops (`drop:True`). No in-RAM pending state.
- **D4 — `milestone_id` == the confirm's `question_id`.** The proposal uses ONE id for both the `engagement.milestone.milestone_id` and the `operator.question`'s reference (carried in `context_refs=["milestone:<id>"]`, since the wire `operator.question` has no `question_id` field — the Task-5 D7 constraint). The confirm's `operator.answer.question_id` echoes it; the assistant matches `operator.answer.question_id` against `pending_milestones[*].milestone_id`.
- **D5 — Loop-avoidance (the assistant now BOTH publishes and consumes `operator.question`/`operator.answer`).** (a) The Task-5 `_on_question` handler MUST skip the assistant's OWN proposal questions — it ignores any `operator.question` whose `context_refs` contains a `milestone:` marker (those are confirms, not operator asks). (b) The Task-6 confirm resolution acts on an `operator.answer` ONLY if its `question_id` matches a live `pending_milestone`; the assistant's own Task-5 `operator.answer` (answering an operator ask) references an operator question id, never a pending-milestone id, so it is naturally ignored — no loop.
- **D6 — Acceptance = deterministic; the live-surface smoke is batched.** The tiering, the reducer dispatch + `apply_milestone`, the pending/commit/drop lifecycle, and the confirm resolution are all deterministic (bus + repo, LLM faked) → the 5 CI gates + the new tests ARE the acceptance. The live surface — an auto-write mutating the TUI's Working-Memory panel, a pending confirm the operator resolves — rides the EXISTING WM panel + the `operator.question`/`operator.answer` channel (whose visual is #28), so its **operator smoke** is added to the runbook and runs in the batched pre-`dev`-merge session (2026-07-20 deferral).

## File Structure

**Create:**
- `redrobin/application/agents/assistant/interpret.py` — the interpretation producer: decide auto-write vs propose, build the `engagement.milestone`/`operator.question` publishes, and resolve a confirm.
- `tests/unit/test_reducer_milestone.py` — the reducer dispatch + `apply_milestone` (commit / pending / drop) over a real repo.
- `tests/unit/agents/assistant/test_interpret.py` — the interpretation cycle/parser + producer + confirm resolution.

**Modify:**
- `redrobin/domain/memory.py` — `PendingMilestone` dataclass + `WorkingMemory.pending_milestones: list[PendingMilestone]` (default empty).
- `redrobin/domain/assistant.py` — `Interpretation` dataclass.
- `redrobin/infrastructure/persistence/working_memory_repo.py` — `apply_milestone(...)` (load→mutate→save); `_from_dict` to load the new field.
- `redrobin/application/working_memory_reducer.py` — the `engagement.milestone` `_dispatch` case + `_on_milestone`.
- `redrobin/application/agents/assistant/parser.py` — `Interpretation` parse (`parse_interpretation`, never-raises).
- `redrobin/application/agents/assistant/agent.py` — the interpretation system prompt + `run_interpretation_cycle`.
- `redrobin/application/agents/assistant/assistant.py` — run the interpretation cycle on `enrichment.completed`; consume `operator.answer` for confirms; the Task-5 `_on_question` skip for own proposals.
- `scripts/smoke/rr-assistant-smoke-runbook.md` — a `[Task 6]` checkpoint (batched smoke).

**Untouched (verify):** the reactive/proactive `run_cycle` + its handlers (D1 keeps interpretation separate), `triggers.py`, `journal_service.py`, `lessons.py`, the TUI.

---

### Task 6.1: Reducer dispatch + `apply_milestone` + `pending_milestones`

The single-writer path: the reducer applies `engagement.milestone` to WM (commit) or records/drops a pending proposal.

**Files:**
- Modify: `redrobin/domain/memory.py`, `redrobin/infrastructure/persistence/working_memory_repo.py`, `redrobin/application/working_memory_reducer.py`
- Test: create `tests/unit/test_reducer_milestone.py`

**Interfaces:**
- Produces:
  - `@dataclass class PendingMilestone: milestone_id: str; phase: str; status: str; progress_pct: int; prompt: str`
  - `WorkingMemory.pending_milestones: list[PendingMilestone] = field(default_factory=list)`
  - `WorkingMemoryRepo.apply_milestone(self, *, phase: str, milestone_id: str, status: str, progress_pct: int, pending: bool = False, drop: bool = False, prompt: str = "") -> None`
  - reducer `_on_milestone(payload)` + the `_dispatch` case.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reducer_milestone.py
from __future__ import annotations

from redrobin.domain.memory import Objective, PendingMilestone, WorkingMemory
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def _repo(tmp_path) -> WorkingMemoryRepo:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(WorkingMemory(engagement_id="eng-A", objectives=[Objective(id="obj1", goal="Domain Admin")]))
    return repo


def test_commit_milestone_writes_phase_objective_killchain(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(phase="lateral-movement", milestone_id="obj1", status="COMPLETE", progress_pct=100)
    wm = repo.load()
    assert wm.current_phase == "lateral-movement"
    assert wm.objectives[0].status == "COMPLETE"
    assert wm.objectives[0].progress_pct == 100
    assert wm.kill_chain_status["obj1"] == "COMPLETE"


def test_pending_milestone_is_recorded_not_applied(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(
        phase="lateral-movement", milestone_id="obj1", status="COMPLETE", progress_pct=100,
        pending=True, prompt="Objective obj1 -> COMPLETE?",
    )
    wm = repo.load()
    assert wm.current_phase != "lateral-movement"  # NOT applied
    assert wm.objectives[0].status == "not_started"  # unchanged
    assert [p.milestone_id for p in wm.pending_milestones] == ["obj1"]
    assert wm.pending_milestones[0].prompt == "Objective obj1 -> COMPLETE?"


def test_commit_removes_matching_pending(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(phase="p", milestone_id="obj1", status="COMPLETE", progress_pct=100, pending=True, prompt="?")
    repo.apply_milestone(phase="lateral", milestone_id="obj1", status="COMPLETE", progress_pct=100)  # commit
    wm = repo.load()
    assert wm.pending_milestones == []  # pending consumed
    assert wm.objectives[0].status == "COMPLETE"


def test_drop_removes_pending_without_applying(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_milestone(phase="p", milestone_id="obj1", status="COMPLETE", progress_pct=100, pending=True, prompt="?")
    repo.apply_milestone(phase="", milestone_id="obj1", status="", progress_pct=0, drop=True)  # reject
    wm = repo.load()
    assert wm.pending_milestones == []
    assert wm.objectives[0].status == "not_started"  # never applied


def test_reducer_dispatch_routes_engagement_milestone(tmp_path):
    # the reducer treats engagement.milestone as a mutating (snapshot-re-emitting) event
    repo = _repo(tmp_path)
    from redrobin.application.working_memory_reducer import WorkingMemoryReducer

    reducer = WorkingMemoryReducer.__new__(WorkingMemoryReducer)  # construct minimally
    reducer._repo = repo  # type: ignore[attr-defined]
    reducer._episodic = None  # type: ignore[attr-defined]
    reducer._engagement_id = "eng-A"  # type: ignore[attr-defined]
    handled = reducer._dispatch(
        {"type": "engagement.milestone", "payload": {"phase": "recon", "milestone_id": "obj1", "status": "in_progress", "progress_pct": 40}}
    )
    assert handled is True
    assert repo.load().current_phase == "recon"
```

_Implementer: if constructing the reducer via `__new__` proves brittle (extra required attrs), instead read `WorkingMemoryReducer.__init__` and build it the way the existing reducer tests (`tests/integration/test_*reducer*` / `test_events_to_memory*`) do — match the real constructor. The point of the last test is only that `_dispatch` returns True and applies for `engagement.milestone`._

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/test_reducer_milestone.py -v`
Expected: FAIL — `cannot import name 'PendingMilestone'` / no `apply_milestone`.

- [ ] **Step 3: Write the implementation**

In `redrobin/domain/memory.py`, add `PendingMilestone` (before `WorkingMemory`) + the field:

```python
@dataclass
class PendingMilestone:
    """A close/commit interpretation the assistant proposed and is awaiting the
    operator's binary confirm (spec §6.C). Persisted in WM so continuity survives
    a restart — the assistant holds NO live pending state. Read back by id on the
    operator's answer; rendered by the confirm affordance (deferred UI, #28)."""

    milestone_id: str
    phase: str
    status: str
    progress_pct: int
    prompt: str
```

Add to `WorkingMemory` (with the other `field(default_factory=list)` fields):

```python
    pending_milestones: list[PendingMilestone] = field(default_factory=list)
```

In `redrobin/infrastructure/persistence/working_memory_repo.py`:
- `_from_dict`: hydrate `pending_milestones` from the loaded dict (mirror how it hydrates `objectives`/`findings` — a list of `PendingMilestone(**d)`; tolerate a missing key → `[]`).
- Add `apply_milestone` (load→mutate→save, single-writer):

```python
    def apply_milestone(
        self, *, phase: str, milestone_id: str, status: str, progress_pct: int,
        pending: bool = False, drop: bool = False, prompt: str = "",
    ) -> None:
        """Apply an engagement.milestone (spec §6.C). ``pending`` records a proposal
        without applying it; ``drop`` removes a pending proposal (a rejected confirm);
        otherwise COMMIT: set current_phase, the objective's status/progress_pct (by
        id), kill_chain_status, and consume any matching pending. Single-writer."""
        wm = self.load()
        wm.pending_milestones = [p for p in wm.pending_milestones if p.milestone_id != milestone_id]
        if drop:
            self.save(wm)
            return
        if pending:
            wm.pending_milestones.append(
                PendingMilestone(milestone_id=milestone_id, phase=phase, status=status,
                                 progress_pct=progress_pct, prompt=prompt)
            )
            self.save(wm)
            return
        # commit
        if phase:
            wm.current_phase = phase
        for obj in wm.objectives:
            if obj.id == milestone_id:
                obj.status = status
                obj.progress_pct = progress_pct
        if milestone_id:
            wm.kill_chain_status[milestone_id] = status
        self.save(wm)
```

(Import `PendingMilestone` where `WorkingMemory` etc. are imported.)

In `redrobin/application/working_memory_reducer.py` `_dispatch`, add the case (after the `working_memory.deltas` branch):

```python
        elif etype == "engagement.milestone":
            self._on_milestone(payload)
            return True
```

Add the handler:

```python
    def _on_milestone(self, payload: dict[str, Any]) -> None:
        """Interpretation write (spec §6.C) — the assistant proposes, the reducer
        (single writer) applies. Never crashes the reducer."""
        try:
            self._repo.apply_milestone(
                phase=str(payload.get("phase") or ""),
                milestone_id=str(payload.get("milestone_id") or ""),
                status=str(payload.get("status") or ""),
                progress_pct=int(payload.get("progress_pct") or 0),
                pending=bool(payload.get("pending")),
                drop=bool(payload.get("drop")),
                prompt=str(payload.get("prompt") or ""),
            )
        except Exception:
            logger.exception("reducer: engagement.milestone apply failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/test_reducer_milestone.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add redrobin/domain/memory.py redrobin/infrastructure/persistence/working_memory_repo.py redrobin/application/working_memory_reducer.py tests/unit/test_reducer_milestone.py
git commit -m "feat(interpret): engagement.milestone reducer dispatch + apply_milestone (commit/pending/drop, single-writer)"
```

---

### Task 6.2: Interpretation cycle + parser

The assistant's read: a focused cycle → `Interpretation` (or None), two-tier via `needs_confirm`.

**Files:**
- Modify: `redrobin/domain/assistant.py`, `redrobin/application/agents/assistant/parser.py`, `redrobin/application/agents/assistant/agent.py`
- Test: create `tests/unit/agents/assistant/test_interpret.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Interpretation: phase: str; milestone_id: str; status: str; progress_pct: int; needs_confirm: bool; prompt: str`
  - `parse_interpretation(raw: str) -> Interpretation | None` (never-raises)
  - `run_interpretation_cycle(*, llm, tools, wm_snapshot, l5_summary, max_tokens=1024, wm_max_items=...) -> Interpretation | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/assistant/test_interpret.py
from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.agent import run_interpretation_cycle
from redrobin.application.agents.assistant.parser import parse_interpretation
from redrobin.domain.assistant import Interpretation


def test_parse_soft_interpretation_no_confirm():
    raw = '{"phase": "lateral-movement", "milestone_id": "obj1", "status": "in_progress", "progress_pct": 40, "needs_confirm": false, "prompt": ""}'
    it = parse_interpretation(raw)
    assert it == Interpretation("lateral-movement", "obj1", "in_progress", 40, False, "")


def test_parse_commit_interpretation_needs_confirm():
    raw = '{"phase": "", "milestone_id": "obj1", "status": "COMPLETE", "progress_pct": 100, "needs_confirm": true, "prompt": "Objective obj1 done?"}'
    it = parse_interpretation(raw)
    assert it is not None and it.needs_confirm is True and it.prompt == "Objective obj1 done?"


def test_parse_interpretation_none_when_no_change():
    assert parse_interpretation('{"interpretation": null}') is None
    assert parse_interpretation('{"needs_confirm": false}') is None  # no milestone_id/phase -> nothing to write
    assert parse_interpretation("not json") is None
    assert parse_interpretation("42") is None
    assert parse_interpretation("") is None


def test_parse_clamps_progress_and_defaults():
    raw = '{"phase": "recon", "milestone_id": "", "status": "in_progress", "progress_pct": 250, "needs_confirm": false}'
    it = parse_interpretation(raw)
    assert it is not None and it.progress_pct == 100  # clamped 0..100
    assert it.prompt == ""  # defaulted


def test_parse_non_coercible_progress_is_zero():
    it = parse_interpretation('{"milestone_id": "o", "status": "x", "progress_pct": {"bad": 1}, "needs_confirm": false}')
    assert it is not None and it.progress_pct == 0  # int() TypeError guarded, never raises


def test_parse_confirm_without_milestone_id_downgrades_to_soft():
    # a confirm needs an id to be trackable — without one it becomes a soft auto-write
    it = parse_interpretation('{"phase": "recon", "milestone_id": "", "status": "x", "progress_pct": 50, "needs_confirm": true}')
    assert it is not None and it.needs_confirm is False


@pytest.mark.asyncio
async def test_run_interpretation_cycle_parses():
    class _LLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            return '{"phase": "recon", "milestone_id": "obj1", "status": "in_progress", "progress_pct": 30, "needs_confirm": false, "prompt": ""}'

    it = await run_interpretation_cycle(llm=_LLM(), tools=[], wm_snapshot={"objectives": []}, l5_summary="")
    assert it is not None and it.phase == "recon"
```

- [ ] **Step 2: Run tests to verify they fail** — Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_interpret.py -v` → FAIL (import errors).

- [ ] **Step 3: Write the implementation**

In `redrobin/domain/assistant.py`, add:

```python
@dataclass(frozen=True)
class Interpretation:
    """The assistant's read of the shared engagement picture (spec §6.C): a phase
    nudge / objective status+progress / kill-chain read. ``needs_confirm`` gates the
    tier — False = auto-write (soft/reversible), True = propose-and-confirm (close/
    commit). ``prompt`` is the binary-confirm question shown for the True tier."""

    phase: str
    milestone_id: str
    status: str
    progress_pct: int
    needs_confirm: bool
    prompt: str
```

Add `Interpretation` to `__all__`.

In `redrobin/application/agents/assistant/parser.py`, add (import `Interpretation`):

```python
def parse_interpretation(raw: str) -> Interpretation | None:
    """Parse the interpretation cycle's output, or None (no material change / silent).
    NEVER raises. Requires at least one of phase/milestone_id (else there is nothing
    to write). progress_pct clamped 0..100."""
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable interpretation JSON — no write")
        return None
    if not isinstance(data, dict):
        return None
    phase = str(data.get("phase") or "").strip()
    milestone_id = str(data.get("milestone_id") or "").strip()
    if not phase and not milestone_id:
        return None  # nothing to write
    try:
        progress = int(data.get("progress_pct") or 0)
    except (TypeError, ValueError):
        progress = 0
    progress = max(0, min(100, progress))
    # A confirm is always ABOUT a specific objective/kill-chain id — without one it
    # can't be tracked/resolved, so downgrade it to a soft auto-write (prevents an
    # untrackable empty-id pending that a plain operator answer could collide with).
    needs_confirm = bool(data.get("needs_confirm")) and bool(milestone_id)
    return Interpretation(
        phase=phase,
        milestone_id=milestone_id,
        status=str(data.get("status") or "").strip(),
        progress_pct=progress,
        needs_confirm=needs_confirm,
        prompt=str(data.get("prompt") or "").strip(),
    )
```

Add `parse_interpretation` to `__all__`.

In `redrobin/application/agents/assistant/agent.py`, add the prompt + cycle (import `parse_interpretation`, `Interpretation`):

```python
_INTERPRET_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "Read the engagement state and decide whether the SHARED PICTURE should be updated: the "
    "current_phase, an objective's status/progress_pct, or a kill-chain milestone. Reason only "
    "over the given state — never invent progress.\n\n"
    "Return ONLY JSON, no prose/fences. If nothing materially changed, return "
    '{"milestone_id": "", "phase": ""} (or null). Otherwise:\n'
    '{"phase": "the current phase, or \\"\\" to leave it",\n'
    ' "milestone_id": "the objective/kill-chain id this is about, or \\"\\"",\n'
    ' "status": "the new status (e.g. in_progress, COMPLETE)",\n'
    ' "progress_pct": 0-100,\n'
    ' "needs_confirm": true ONLY for a change that CLOSES or COMMITS (an objective -> COMPLETE, a '
    'kill-chain goal reached); false for a soft, reversible read (a phase nudge, a progress bump),\n'
    ' "prompt": "if needs_confirm, a one-line yes/no question for the operator; else \\"\\""}'
)


def build_interpret_context(
    *, wm_snapshot: dict[str, Any], l5_summary: str, wm_max_items: int = _DEFAULT_WM_MAX_ITEMS
) -> str:
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    return "\n".join([
        "# Engagement working memory (L2)",
        json.dumps(capped, ensure_ascii=False, sort_keys=True),
        "",
        "# Story so far (L5)",
        l5_summary or "(no summary yet)",
        "",
        "Given this, should the shared picture be updated? Return the interpretation JSON or null.",
    ])


async def run_interpretation_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    max_tokens: int = 1024,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> Interpretation | None:
    """Run ONE interpretation cycle → Interpretation or None (no material change)."""
    raw = await llm.run(
        system=_INTERPRET_SYSTEM_PROMPT,
        user_message=build_interpret_context(
            wm_snapshot=wm_snapshot, l5_summary=l5_summary, wm_max_items=wm_max_items,
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    return parse_interpretation(raw)
```

Add `build_interpret_context`, `run_interpretation_cycle` to `__all__`. Type the signature (`llm: AgentLLM`, `tools: list[AgentTool]`, `wm_snapshot: dict[str, Any]`, `l5_summary: str`, `-> Interpretation | None`).

- [ ] **Step 4: Run test to verify it passes** — Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_interpret.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add redrobin/domain/assistant.py redrobin/application/agents/assistant/parser.py redrobin/application/agents/assistant/agent.py tests/unit/agents/assistant/test_interpret.py
git commit -m "feat(interpret): interpretation cycle + never-raises parse_interpretation (two tiers)"
```

---

### Task 6.3: The producer + confirm resolution in the Assistant

Wire it in: auto-write soft reads, propose close/commit changes, and resolve the operator's confirm — all NON-BLOCKING, continuity via WM.

**Files:**
- Create: `redrobin/application/agents/assistant/interpret.py`
- Modify: `redrobin/application/agents/assistant/assistant.py`
- Test: `tests/unit/agents/assistant/test_interpret.py` (append)

**Interfaces:**
- Consumes: `run_interpretation_cycle`/`Interpretation` (6.2).
- Produces (in `interpret.py`, pure helpers so the wiring is testable without a live loop):
  - `def milestone_events(it: Interpretation) -> list[dict[str, Any]]` — the bus event(s) to publish for an interpretation: for soft → one commit `engagement.milestone`; for confirm → a `pending` `engagement.milestone` + an `operator.question` (context_refs `["milestone:<id>"]`).
  - `def resolve_confirm(*, answer_payload, pending_milestones) -> dict[str, Any] | None` — given an `operator.answer` + the WM's pending list, return the `engagement.milestone` to publish (commit if affirmative, drop if negative), or None if the answer matches no pending milestone.
  - `def is_own_proposal_question(question_payload) -> bool` — True if an `operator.question` is the assistant's own confirm (its `context_refs` has a `milestone:` marker) — used by the Task-5 handler to skip it.

- [ ] **Step 1: Write the failing tests** (append to `test_interpret.py`)

```python
from redrobin.application.agents.assistant.interpret import (
    is_own_proposal_question, milestone_events, resolve_confirm,
)
from redrobin.domain.memory import PendingMilestone


def test_soft_interpretation_emits_one_commit_milestone():
    it = Interpretation("recon", "obj1", "in_progress", 40, False, "")
    evts = milestone_events(it)
    assert len(evts) == 1
    assert evts[0]["type"] == "engagement.milestone"
    assert evts[0]["payload"]["milestone_id"] == "obj1"
    assert not evts[0]["payload"].get("pending")


def test_confirm_interpretation_emits_pending_milestone_and_question():
    it = Interpretation("", "obj1", "COMPLETE", 100, True, "Objective obj1 done?")
    evts = milestone_events(it)
    types = [e["type"] for e in evts]
    assert types == ["engagement.milestone", "operator.question"]
    assert evts[0]["payload"]["pending"] is True
    assert evts[1]["payload"]["context_refs"] == ["milestone:obj1"]
    assert evts[1]["payload"]["text"] == "Objective obj1 done?"


def test_resolve_confirm_yes_commits():
    pend = [PendingMilestone("obj1", "lateral", "COMPLETE", 100, "?")]
    evt = resolve_confirm(answer_payload={"question_id": "obj1", "text": "yes, do it"}, pending_milestones=pend)
    assert evt is not None and evt["type"] == "engagement.milestone"
    assert evt["payload"]["milestone_id"] == "obj1" and not evt["payload"].get("pending")
    assert not evt["payload"].get("drop")


def test_resolve_confirm_no_drops():
    pend = [PendingMilestone("obj1", "lateral", "COMPLETE", 100, "?")]
    evt = resolve_confirm(answer_payload={"question_id": "obj1", "text": "no, not yet"}, pending_milestones=pend)
    assert evt is not None and evt["payload"]["drop"] is True


def test_resolve_confirm_unmatched_answer_is_ignored():
    assert resolve_confirm(answer_payload={"question_id": "other", "text": "yes"}, pending_milestones=[]) is None


def test_resolve_confirm_empty_qid_never_matches():
    # an operator ask carries no question_id (-> "") — it must NOT collide with an empty-id pending
    pend = [PendingMilestone("", "p", "COMPLETE", 100, "?")]
    assert resolve_confirm(answer_payload={"question_id": "", "text": "yes"}, pending_milestones=pend) is None


def test_resolve_confirm_negation_wins_and_ambiguous_leaves_pending():
    pend = [PendingMilestone("obj1", "lateral", "COMPLETE", 100, "?")]
    # "no, not yet" -> drop (the "y" in "yet" must NOT read as affirmative)
    assert resolve_confirm(answer_payload={"question_id": "obj1", "text": "no, not yet"}, pending_milestones=pend)["payload"]["drop"] is True
    # ambiguous -> None (leave the pending alive; never guess a commit)
    assert resolve_confirm(answer_payload={"question_id": "obj1", "text": "hmm, interesting"}, pending_milestones=pend) is None


def test_is_own_proposal_question():
    assert is_own_proposal_question({"context_refs": ["milestone:obj1"]}) is True
    assert is_own_proposal_question({"context_refs": ["host:DC01"]}) is False
    assert is_own_proposal_question({"text": "what's my path to DA?"}) is False
```

- [ ] **Step 2: Run to verify red** → FAIL (no `interpret` module).

- [ ] **Step 3: Write `interpret.py`**

```python
"""Interpretation producer + confirm resolution (spec §6.C). Pure helpers — the
Assistant wires them into the bus. The assistant NEVER writes WM; it emits
engagement.milestone events the reducer applies (single-writer)."""

from __future__ import annotations

import re
from typing import Any

from redrobin.domain.assistant import Interpretation
from redrobin.domain.memory import PendingMilestone

# Word-set matching (NOT substring — "y" in "yet" was a reject-becomes-commit bug).
# Negation is checked FIRST so the safe default is never-commit.
_AFFIRMATIVE = {"yes", "y", "yeah", "yep", "yup", "confirm", "ok", "okay", "approved", "sí", "si"}
_NEGATIVE = {"no", "n", "nope", "not", "cancel", "reject", "stop", "wait", "later", "deny", "dont"}


def _milestone_payload(it: Interpretation, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": it.phase, "milestone_id": it.milestone_id,
        "status": it.status, "progress_pct": it.progress_pct,
    }
    payload.update(extra)
    return payload


def milestone_events(it: Interpretation) -> list[dict[str, Any]]:
    """The bus events for an interpretation. Soft (needs_confirm=False) → one commit
    milestone. Close/commit → a pending milestone (persisted for continuity) + an
    operator.question (the binary ask; context_refs mark it as OUR proposal)."""
    if not it.needs_confirm:
        return [{"type": "engagement.milestone", "payload": _milestone_payload(it)}]
    return [
        {"type": "engagement.milestone", "payload": _milestone_payload(it, pending=True, prompt=it.prompt)},
        {"type": "operator.question", "payload": {"text": it.prompt, "context_refs": [f"milestone:{it.milestone_id}"]}},
    ]


def resolve_confirm(
    *, answer_payload: dict[str, Any], pending_milestones: list[PendingMilestone]
) -> dict[str, Any] | None:
    """Resolve an operator.answer against the persisted pending proposals. Returns the
    engagement.milestone to publish (commit if clearly affirmative, drop if clearly
    negative), or None if it matches no pending milestone OR the answer is ambiguous
    (leave the pending alive — never guess a close/commit). An empty question_id (a
    plain operator ask carries none) never matches — no loop, no empty-id collision."""
    qid = str(answer_payload.get("question_id") or "")
    if not qid:
        return None
    match = next((p for p in pending_milestones if p.milestone_id == qid), None)
    if match is None:
        return None
    words = set(re.findall(r"[a-zà-ÿ']+", str(answer_payload.get("text") or "").lower()))
    if words & _NEGATIVE:  # negation FIRST — the safe default is never-commit
        return {"type": "engagement.milestone", "payload": {
            "milestone_id": match.milestone_id, "phase": "", "status": "", "progress_pct": 0, "drop": True}}
    if words & _AFFIRMATIVE:
        return {"type": "engagement.milestone", "payload": {
            "phase": match.phase, "milestone_id": match.milestone_id,
            "status": match.status, "progress_pct": match.progress_pct}}
    return None  # ambiguous — leave the pending alive, don't commit on a guess


def is_own_proposal_question(question_payload: dict[str, Any]) -> bool:
    """True if an operator.question is the assistant's OWN confirm proposal (so the
    conversational handler skips it — it is not an operator ask)."""
    refs = question_payload.get("context_refs")
    return isinstance(refs, list) and any(str(r).startswith("milestone:") for r in refs)


__all__ = ["milestone_events", "resolve_confirm", "is_own_proposal_question"]
```

- [ ] **Step 4: Run to verify green** → PASS.

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/interpret.py tests/unit/agents/assistant/test_interpret.py
git commit -m "feat(interpret): pure producer + confirm-resolution helpers (auto-write / propose / resolve)"
```

---

### Task 6.4: Assistant wiring + Task-5 loop guard + smoke checkpoint

Run the interpretation cycle on `enrichment.completed`; resolve confirms on `operator.answer`; make the Task-5 handler skip own proposals.

**Files:**
- Modify: `redrobin/application/agents/assistant/assistant.py`, `scripts/smoke/rr-assistant-smoke-runbook.md`
- Test: `tests/unit/agents/assistant/test_interpret.py` (append integration-style)

**Interfaces:** Consumes `run_interpretation_cycle` (6.2), `milestone_events`/`resolve_confirm`/`is_own_proposal_question` (6.3).

- [ ] **Step 1: Write the failing tests** (append; reuse the module's EventBus/Assistant test helpers)

```python
import asyncio
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus


class _InterpretLLM:
    def __init__(self, payload): self._p = payload
    async def run(self, *, system, user_message, tools, max_tokens=2048):
        # reactive/answer cycles stay silent; the interpretation cycle returns the milestone
        if "should the shared picture be updated" in user_message:
            return self._p
        return '{"speak": false}'


def _assistant(bus, llm, snapshot=None):
    a = Assistant(bus=bus, llm=llm, tools=[], trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
                  summary_reader=lambda: "recon done", enabled=True)
    a._wm_snapshot = snapshot if snapshot is not None else {"objectives": [{"id": "obj1"}]}
    return a


@pytest.mark.asyncio
async def test_soft_interpretation_publishes_commit_milestone(tmp_path):
    bus = EventBus(); sub = await bus.subscribe()
    llm = _InterpretLLM('{"phase":"recon","milestone_id":"obj1","status":"in_progress","progress_pct":30,"needs_confirm":false,"prompt":""}')
    a = _assistant(bus, llm)
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run()); await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}})
    got = None
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "engagement.milestone":
            got = evt
    assert got is not None and got["payload"]["milestone_id"] == "obj1" and not got["payload"].get("pending")
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_confirm_answer_commits_pending(tmp_path):
    bus = EventBus(); sub = await bus.subscribe()
    a = _assistant(bus, _InterpretLLM('{"speak": false}'),
                   snapshot={"objectives": [{"id": "obj1"}], "pending_milestones": [
                       {"milestone_id": "obj1", "phase": "lateral", "status": "COMPLETE", "progress_pct": 100, "prompt": "?"}]})
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run()); await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.answer", "payload": {"question_id": "obj1", "text": "yes"}})
    got = None
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "engagement.milestone":
            got = evt
    assert got is not None and got["payload"]["milestone_id"] == "obj1" and not got["payload"].get("pending")
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_interpretation_survives_cycle_exception(tmp_path):
    class _Boom:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            if "should the shared picture be updated" in user_message:
                raise RuntimeError("boom")
            return '{"speak": false}'
    bus = EventBus(); await bus.subscribe()
    a = _assistant(bus, _Boom())
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run()); await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "enrichment.completed", "payload": {"command": "x", "beacon_id": "b1"}})
    await asyncio.sleep(0.05)
    assert not task.done()
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_confirm_malformed_pending_survives(tmp_path):
    # a malformed pending dict (missing fields) in the cache is skipped, not crashed
    bus = EventBus(); await bus.subscribe()
    a = _assistant(bus, _InterpretLLM('{"speak": false}'),
                   snapshot={"objectives": [], "pending_milestones": [{"milestone_id": "obj1"}]})
    started = asyncio.Event(); a.on_started = started.set
    task = asyncio.create_task(a.run()); await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.answer", "payload": {"question_id": "obj1", "text": "yes"}})
    await asyncio.sleep(0.05)
    assert not task.done()  # PendingMilestone(**p) TypeError skipped, loop survives
    await bus.publish({"_type": "_close"}); await asyncio.wait_for(task, 1.0)
```

- [ ] **Step 2: Run to verify red** → FAIL.

- [ ] **Step 3: Wire the Assistant** (`assistant.py`)

Imports: `from redrobin.application.agents.assistant.agent import ..., run_interpretation_cycle`; `from redrobin.application.agents.assistant.interpret import is_own_proposal_question, milestone_events, resolve_confirm`.

In `_on_enrichment`, AFTER the reactive publish/return, run the interpretation (guarded, never-crash). Simplest: at the END of the run-loop's `enrichment.completed` handling, after `_on_enrichment`, add a call to a new `_safe_interpret()`:

```python
                if etype != "enrichment.completed":
                    ...
                self._last_beat = self._clock()
                try:
                    await self._on_enrichment(evt.get("payload") or {})
                except Exception:
                    logger.exception("assistant failed to process enrichment.completed")
                if self._material_pending:
                    self._material_pending = False
                    await self._safe_proactive("material_change")
                await self._safe_interpret()   # <-- NEW: the interpretation write
```

Add an `operator.answer` branch (BEFORE the `enrichment.completed` guard, alongside `operator.question`):

```python
                if etype == "operator.answer":
                    self._last_beat = self._clock()  # resolving a confirm is activity — defer the lull
                    try:
                        await self._on_confirm(evt.get("payload") or {})
                    except Exception:
                        logger.exception("assistant failed to process operator.answer")
                    continue
```

Add `self._last_interp_sig: str | None = None` to `__init__` (the novelty guard, below).

Add the handlers. `_safe_interpret` wraps its ENTIRE body in one try/except (never-crash — the publish/log must be inside it, mirroring `_safe_proactive`), gates on state novelty so it doesn't re-run/re-propose every command (signal-not-noise + cost), and skips re-proposing an id already pending:

```python
    async def _safe_interpret(self) -> None:
        if self._llm is None or not self._enabled or not self._wm_snapshot:
            return
        # Novelty gate: only interpret when the state changed since the last interpretation
        # (bounds cost + stops re-firing the same confirm every command — signal-not-noise).
        sig = state_signature(self._wm_snapshot)
        if sig == self._last_interp_sig:
            return
        self._last_interp_sig = sig
        try:
            try:
                l5 = self._summary_reader()
            except Exception:
                logger.exception("assistant: summary_reader failed; empty L5")
                l5 = ""
            it = await run_interpretation_cycle(
                llm=self._llm, tools=self._tools, wm_snapshot=self._wm_snapshot, l5_summary=l5,
                max_tokens=self._max_tokens, wm_max_items=self._wm_max_items,
            )
            if it is None:
                return
            # Don't re-publish a confirm for an id already pending (no duplicate operator.question).
            if it.needs_confirm and any(
                str(p.get("milestone_id")) == it.milestone_id
                for p in (self._wm_snapshot.get("pending_milestones") or [])
                if isinstance(p, dict)
            ):
                return
            for evt in milestone_events(it):
                await self._bus.publish(evt)
            logger.info("assistant: interpretation %s milestone=%s confirm=%s",
                        "proposed" if it.needs_confirm else "auto-write", it.milestone_id, it.needs_confirm)
        except Exception:
            logger.exception("assistant: interpretation write failed")

    async def _on_confirm(self, payload: dict[str, Any]) -> None:
        pend_raw = self._wm_snapshot.get("pending_milestones") or []
        pend = []
        for p in pend_raw:
            if isinstance(p, dict):
                try:
                    pend.append(PendingMilestone(**p))
                except TypeError:
                    continue  # a malformed pending dict is skipped, never crashes
        evt = resolve_confirm(answer_payload=payload, pending_milestones=pend)
        if evt is None:
            return  # not a confirm for one of our pending proposals — ignore (no loop)
        await self._bus.publish(evt)
        logger.info("assistant: confirm resolved milestone=%s drop=%s",
                    evt["payload"].get("milestone_id"), evt["payload"].get("drop", False))
```

(Import `PendingMilestone` from `redrobin.domain.memory`.)

In the EXISTING Task-5 `_on_question` (or its run-loop branch), skip the assistant's own proposals — at the top of `_on_question`:

```python
        if is_own_proposal_question(payload):
            return  # our own confirm proposal, not an operator ask (no loop)
```

- [ ] **Step 4: Run tests + the reactive/proactive/conversational suites to verify green**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/ tests/integration/test_assistant.py -v`
Expected: PASS (interpretation + confirm + never-crash; the existing reactive/proactive/conversational tests unregressed).

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/agents/assistant/assistant.py tests/unit/agents/assistant/test_interpret.py
git commit -m "feat(interpret): wire interpretation write + non-blocking confirm resolution into the Assistant"
```

- [ ] **Step 6: Smoke checkpoint + the 5 CI gates**

Append a `## [Task 6 (#27)] Interpretation-write` section to `scripts/smoke/rr-assistant-smoke-runbook.md` (batched smoke): run commands that advance an objective → EXPECT the Working-Memory panel's `current_phase`/objective `progress` update ("the assistant's read", reversible); trigger a close/commit → EXPECT a pending confirm (over `operator.question`, rendered by #28's Chat pane) that the operator resolves yes/no, the beacon never held. Commit: `docs(smoke): add interpretation-write operator checkpoints (Task 6)`.

Then run the 5 CI gates (verbatim) and confirm green.

---

## Acceptance / Definition of Done (Task 6 / #27)

- [ ] **5 CI gates green.**
- [ ] **TDD red→green** for: the reducer dispatch + `apply_milestone` commit/pending/drop (6.1); the interpretation cycle/parser (6.2); the producer + confirm resolution + loop guard (6.3); the Assistant wiring + never-crash (6.4).
- [ ] **Two tiers** — soft reads **auto-write** (reversible via a later operator override; the "assistant's read" *label* is UI, deferred to #28); close/commit go **propose-and-confirm**; the confirm is **binary, async, NON-BLOCKING** — a pending change by id, the beacon never held, resolved in a later cycle from the persisted WM.
- [ ] **Assistant never writes WM directly** — it publishes `engagement.milestone`; the reducer's new dispatch writes it (single-writer).
- [ ] **Never-crash + degraded-None** — no key → no writes; a raising interpretation/confirm cycle logged + swallowed; the reducer's milestone apply guarded; the confirm never freezes.
- [ ] **No loop** — the Task-5 handler skips own proposals; a confirm resolves only against a live pending milestone (D5).
- [ ] **Observability** — `engagement.milestone` events + `assistant:` logs (auto-write/proposed/confirmed/dropped).
- [ ] **Acceptance form (D6)** — deterministic → CI gates + tests; the live-surface smoke (WM panel updates, a resolved confirm) → runbook checkpoint, batched pre-`dev`-merge.
- [ ] **Atomic conventional commits, no AI co-author trailer.**
- [ ] **Pillars** — ① interpretation via the existing `engagement.milestone` → reducer seam, no new writer; ② single-writer + non-blocking async confirm, coherent with the eliminated sync gate; ③ writes labeled + reversible, the operator always sees/overrides.

## Documented limitations / deferred (in-scope, by design)

- **The confirm-affordance visual = #28** (the Chat pane renders `operator.question`/`operator.answer`, incl. a pending confirm). Task 6 delivers the mechanics + the bus-level path; the operator resolves via `operator.answer` (tested here at the bus level).
- **"Labeled the assistant's read" is UI, deferred to #28.** The commit writes `current_phase`/objective status/`kill_chain_status` with no data-layer provenance marker; the *reversibility* holds (WM is a mutable operator-curated file, a later edit/commit overrides), but presenting the value AS the assistant's inference is the WM-panel's job (#28), not this backend. The DoD's "labeled" is satisfied by #28, not Task 6.
- **Interpretation is novelty-gated** (audit fix) — the cycle runs only when `state_signature` changed since the last interpretation (not every command), so cost is bounded and a pending confirm is not re-asked each command; an already-pending id is never re-proposed. (Folding it into the reactive cycle to share the call is a future refinement.)
- **Restart continuity is event-gated** (audit D-a/D3): `pending_milestones` is persisted in `working_memory.json`, but the assistant's read-cache (`_wm_snapshot`) only hydrates from a reducer-emitted `working_memory_snapshot` (after a mutating event), NOT at boot. So after a daemon restart with an outstanding confirm, the operator's answer resolves only once the next mutating event (a beacon beat, in a live engagement within a beat) re-emits the snapshot. This matches the assistant's pre-existing lazy-bootstrap; a boot-time snapshot re-emit would fully close it (deferred refinement).
- **A rejected confirm does NOT (yet) feed the learning loop.** §6.C names "escalating a confirm into a typed reply = a correction = feeds learning"; Task 6 DROPS a rejected pending but does not capture a lesson (Task-5 capture lives in the `operator.question` handler, not `operator.answer`). Routing a typed rejection through lesson-capture is deferred to #28 (where a typed reply is re-surfaced). Issue #27's DoD does not require it.
- **`operator.question` wire `question_id`** — same Task-5 D7 constraint: our proposals carry the ref in `context_refs`; #28 makes the schema decision.

## Self-Review (author checklist — completed)

1. **Spec coverage (§6.C):** two tiers (auto-write/propose-and-confirm) → D2/6.2/6.3; binary non-blocking confirm, pending-by-id, resolved from memory → D3/6.3/6.4; never writes WM directly → 6.1 (reducer single-writer); reuses engagement.milestone + operator.question/answer → D3/D4; continuity in memory not a live session → D3 (WM.pending_milestones, with the event-gated re-hydration limitation documented). §4 reducer dispatch → 6.1. §10 confirm visual + "labeled as the assistant's read" deferred → #28. **Two §6.C properties are honestly DEFERRED (see Documented limitations), not claimed done:** "a rejected confirm feeds the learning loop" (Task 6 drops the pending; lesson-capture on a typed rejection is #28) and "labeled the assistant's read" (a UI presentation, #28). Issue #27's DoD does not itemize either.
2. **Placeholder scan:** every step has runnable code or an exact command; the `...` in 6.4's run-loop points at existing surrounding code to preserve.
3. **Type consistency:** `Interpretation` fields ↔ `parse_interpretation` ↔ `milestone_events` payloads ↔ the reducer's `apply_milestone` kwargs ↔ `PendingMilestone` fields all align; `resolve_confirm` matches `operator.answer.question_id` to `PendingMilestone.milestone_id`; new `apply_milestone` kwargs are keyword-only + defaulted (additive).
