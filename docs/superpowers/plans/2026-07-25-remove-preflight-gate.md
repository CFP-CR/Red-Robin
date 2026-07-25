# Remove the Synchronous OPSEC Pre-Flight Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the pre-flight gate (Layer A Sigma + Layer B LLM judge + the verdict pipeline and its surfaces) and keep only the `.cna` capture that feeds the AI assistant, re-sourcing the episodic/L5 memory from the beacon command stream.

**Architecture:** This is a **deletion**, so it adds one thing and removes everything else. The one addition (Task 1) re-sources L3 Episodes from `beacon.command_input` so the L5 "story so far" — which the assistant reads on every cycle — never loses its input. The deletion then proceeds **top-down through the import graph** (HTTP/WS surfaces → pipeline → engines → domain types + rule data), so every commit leaves a green tree with no orphaned imports. Memory types narrow only after nothing writes the dropped fields.

**Tech Stack:** Python 3.13, FastAPI, asyncio, SQLite, Textual (TUI), Cobalt Strike Aggressor (`.cna`), pytest, `uv`.

## Global Constraints

Every task's requirements implicitly include this section. Values copied from the spec (`docs/superpowers/specs/2026-07-25-remove-preflight-gate-design.md`), `CLAUDE.md`, and `CONTRIBUTING.md`.

- **5 CI gates must pass after every task** (use `uv run --no-sync` — the daemon may be running and locks the venv):
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy redrobin/domain redrobin/application`
  - `uv run lint-imports --config importlinter.cfg` → `Contracts: 4 kept, 0 broken.` (run it plainly — do NOT pipe stdout to `/dev/null` on Windows; rich's cp1252 encode raises a spurious `UnicodeEncodeError`)
  - `REDROBIN_ALLOW_DEGRADED=1 uv run pytest tests/ -q`
- **The suite SHRINKS in this plan.** That is expected: 21 gate test files are deleted with their subjects. Record the count in each task's report. **Never weaken or skip a surviving test to make a deletion pass** — if a surviving test fails, something still depends on what you deleted, and that is a finding to report.
- **`tests/` has FOUR directories, not two:** `tests/unit/`, `tests/integration/`, **`tests/e2e/`**, and `tests/` root helpers. `pytest tests/` runs all of them, so an e2e journey test that exercises the gate will go red in the middle of a deletion task unless that task removes it. Three e2e files are gate-owned and get deleted (Task 4); two more assert gate details and get edited (Tasks 4 and 6). **Do not forget `tests/e2e/`.**
- **The wire protocol has its own surface under `protocol/`:** JSON Schemas in `protocol/schemas/v0.1/events/` and conformance fixtures in `protocol/fixtures/v0.1/{valid,invalid}/`, walked by `tests/unit/test_event_schemas.py`. `opsec.verdict` has all three, and they go with the event type (Task 2). `validate_event` returns `"no schema registered for event type …"` for an unknown type, so a deleted schema correctly rejects a stale `opsec.verdict` POST rather than crashing.
- **⚠️ THREE overlapping severity vocabularies — only two go. Delete by symbol and by module, NEVER by string match:**
  | Enum | Values | Home | Fate |
  |---|---|---|---|
  | `VerdictAction` | PASS / WARN / BLOCK / ESCALATE | `redrobin/domain/opsec.py` | **deleted** |
  | `RuleSeverity` | INFO / WARN / DANGER | `redrobin/domain/rules.py` | **deleted** |
  | `Severity` | INFO / WARN / DANGER / ESCALATE | `redrobin/domain/events.py` | **KEPT** — it is the assistant's `Recommendation.opsec_risk` |
  `redrobin/tui/textual_ui/chat.py`'s `_RISK_COLORS` is keyed on **`Severity`** values. A grep-and-delete over `"WARN"` or `"ESCALATE"` breaks advisory rendering.
- **The capture path is untouched:** the `.cna`'s `on beacon_input` / `on beacon_initial` / `set BEACON_OUTPUT` → `POST /v1/events` → `events_router` → the bus → the enricher → the reducer → working memory → the assistant. Do not modify any of it except where a task explicitly says so.
- **No replacement gate.** No soft gate, no confirmation prompt, no feature flag to re-enable. If it must return, it returns from git.
- **Ordering is a hard constraint** (spec §7): Task 1 (additive re-source) lands FIRST; the `Episode` narrowing (Task 5) lands only AFTER nothing writes `verdict`/`reason`.
- **TDD** where code is added or changed (Tasks 1, 5, 6): failing test → verify red → implement → verify green → commit. Pure deletions have no red phase — their verification is the green suite plus the absence checks each task names.
- **Atomic, conventional commits**; one logical change per commit. **NO AI co-author trailer.**
- **Four import-linter contracts** hold throughout: `api|tui → application → domain`; `domain` imports nothing from other layers; `application` never imports `api`/`tui`; `api`/`tui` never import `infrastructure` directly.
- Acceptance for the `.cna` (Task 7) is an **operator live smoke**, not CI.

---

### Task 1: Re-source L3 Episodes from `beacon.command_input` (the only additive task)

**Files:**
- Modify: `redrobin/application/working_memory_reducer.py` (add a `beacon.command_input` branch to `_dispatch`; add `_on_command_input`)
- Test: `tests/unit/test_reducer_command_episodes.py` (new)

**Interfaces:**
- Consumes: `EpisodicRepo.record(*, engagement_id: str, correlation_id: str, command: str, verdict: str, reason: str = "", created_at: datetime | None = None) -> Episode` — **as it exists today**; Task 5 narrows it. Pass `verdict=""` and `reason=""` for now.
- Produces: `WorkingMemoryReducer._on_command_input(payload: dict[str, Any]) -> None` — records one Episode per beacon command, mutating no working memory.

**Why `beacon.command_input`:** the `.cna` publishes it on **every** command with `command` and `correlation_id` in the payload (wire: `bid`, `operator`, `command`, `raw`, `task_id?`, `correlation_id?`), and it needs **no LLM**. `enrichment.completed` also carries the command but only fires when the enricher runs, so a degraded boot (no `ANTHROPIC_API_KEY`) would leave L5 with no input — which the deterministic Layer A never did.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_reducer_command_episodes.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.working_memory_reducer import WorkingMemoryReducer
from redrobin.domain.memory import WorkingMemory
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def _reducer(tmp_path, bus):
    wm_repo = WorkingMemoryRepo(tmp_path / "working_memory.json")
    wm_repo.save(WorkingMemory(engagement_id="eng-1"))
    ep_repo = EpisodicRepo(tmp_path / "episodic.db")
    reducer = WorkingMemoryReducer(bus, wm_repo, "eng-1", episodic_repo=ep_repo)
    return reducer, wm_repo, ep_repo


def test_command_input_records_one_episode(tmp_path):
    reducer, _, ep_repo = _reducer(tmp_path, EventBus())
    reducer._dispatch(
        {
            "type": "beacon.command_input",
            "payload": {
                "bid": "b1",
                "operator": "jose",
                "command": "whoami",
                "raw": "whoami /all",
                "correlation_id": "c-1",
            },
        }
    )
    episodes = ep_repo.list_episodes_for_engagement("eng-1")
    assert len(episodes) == 1
    assert episodes[0].command == "whoami"
    assert episodes[0].correlation_id == "c-1"


def test_command_input_does_not_mutate_working_memory(tmp_path):
    # returning False keeps the reducer from broadcasting a full snapshot per command —
    # nothing in the working memory changed, only the episodic log grew
    reducer, wm_repo, _ = _reducer(tmp_path, EventBus())
    before = wm_repo.load()
    mutated = reducer._dispatch(
        {"type": "beacon.command_input", "payload": {"command": "whoami", "correlation_id": "c-1"}}
    )
    assert mutated is False
    after = wm_repo.load()
    assert after.findings == before.findings
    assert after.sessions == before.sessions


def test_command_input_without_a_command_records_nothing(tmp_path):
    reducer, _, ep_repo = _reducer(tmp_path, EventBus())
    reducer._dispatch({"type": "beacon.command_input", "payload": {"correlation_id": "c-1"}})
    assert ep_repo.list_episodes_for_engagement("eng-1") == []


def test_command_input_missing_correlation_id_still_records(tmp_path):
    reducer, _, ep_repo = _reducer(tmp_path, EventBus())
    reducer._dispatch({"type": "beacon.command_input", "payload": {"command": "ls"}})
    episodes = ep_repo.list_episodes_for_engagement("eng-1")
    assert len(episodes) == 1 and episodes[0].correlation_id == ""


def test_a_failing_episodic_write_does_not_crash_the_reducer(tmp_path):
    class _BoomEpisodic:
        def record(self, **kwargs):
            raise OSError("disk full")

    bus = EventBus()
    wm_repo = WorkingMemoryRepo(tmp_path / "working_memory.json")
    wm_repo.save(WorkingMemory(engagement_id="eng-1"))
    reducer = WorkingMemoryReducer(bus, wm_repo, "eng-1", episodic_repo=_BoomEpisodic())
    # must not raise — the reducer survives a failed episode write
    reducer._dispatch({"type": "beacon.command_input", "payload": {"command": "whoami"}})


@pytest.mark.asyncio
async def test_end_to_end_over_the_bus(tmp_path):
    bus = EventBus()
    reducer, _, ep_repo = _reducer(tmp_path, bus)
    started = asyncio.Event()
    reducer.on_started = started.set
    task = asyncio.create_task(reducer.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "beacon.command_input", "payload": {"command": "seatbelt", "correlation_id": "c-9"}}
    )
    for _ in range(100):
        await asyncio.sleep(0.01)
        if ep_repo.list_episodes_for_engagement("eng-1"):
            break
    assert [e.command for e in ep_repo.list_episodes_for_engagement("eng-1")] == ["seatbelt"]
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_reducer_command_episodes.py -q`
Expected: FAIL — `_dispatch` returns `False` for `beacon.command_input` and records nothing, so `len(episodes) == 1` fails.

- [ ] **Step 3: Add the dispatch branch**

In `redrobin/application/working_memory_reducer.py`, inside `_dispatch`, add the branch immediately after the `beacon.initial` branch and before `opsec.verdict`:

```python
        if etype == "beacon.initial":
            self._on_beacon_initial(payload)
            return True
        elif etype == "beacon.command_input":
            # L3 activity log. Returns False: no working-memory field changed, so the
            # reducer must NOT broadcast a full snapshot for every command.
            self._on_command_input(payload)
            return False
        elif etype == "opsec.verdict":
```

- [ ] **Step 4: Add the handler**

Add this method immediately after `_on_beacon_initial`:

```python
    def _on_command_input(self, payload: dict[str, Any]) -> None:
        """Record one Episode per beacon command — the raw activity log L5 narrates from.

        Replaces the verdict stream as L3's source: the gate happened to fire on every
        command, so ``opsec.verdict`` was a de-facto command stream; ``beacon.command_input``
        is the real one and needs no LLM. Never raises — a failed episode write must not
        stop the reducer.
        """
        if self._episodic is None:
            return
        command = str(payload.get("command") or "").strip()
        if not command:
            return
        try:
            self._episodic.record(
                engagement_id=self._engagement_id,
                correlation_id=str(payload.get("correlation_id") or ""),
                command=command,
                verdict="",
                reason="",
            )
        except Exception:
            logger.exception("episodic record failed for command=%s", command)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_reducer_command_episodes.py -q`
Expected: PASS — 6 passed.

- [ ] **Step 6: Run the full suite + gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS, the suite grown by exactly 6. Verdicts still record episodes too — a harmless double-write that Task 2-4 removes.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 7: Commit**

```bash
git add redrobin/application/working_memory_reducer.py tests/unit/test_reducer_command_episodes.py
git commit -m "feat(memory): record L3 episodes from beacon.command_input"
```

---

### Task 2: Delete the gate's HTTP + WS surfaces

**Files:**
- Delete: `redrobin/api/verdicts_router.py` · `redrobin/api/adapter_events_ws.py`
- Delete (wire protocol surface): `protocol/schemas/v0.1/events/opsec_verdict.schema.json` · `protocol/fixtures/v0.1/valid/opsec_verdict.json` · `protocol/fixtures/v0.1/invalid/opsec_verdict.json`
- Modify: `redrobin/api/app.py` (drop both router registrations + their imports) · `redrobin/domain/events.py` (delete the `OpsecVerdictEvent` class and its `Envelope` severity-serialization branch) · `tests/unit/test_event_schemas.py` (drop the `"opsec_verdict"` entry from its type list)
- Delete: `tests/integration/test_verdict_endpoint.py` · `tests/integration/test_verdict_emission.py` · `tests/integration/test_adapter_events_ws.py` · `tests/integration/test_adapter_perms.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: nothing. After this task `verdict_pipeline` has no HTTP entry point, which is what makes Task 3 safe.

**Why these two first:** they are the top of the import graph — nothing in the codebase imports them; `create_app` merely registers them. Deleting them orphans the pipeline without breaking any import.

- [ ] **Step 1: Confirm the two modules are only referenced by `create_app`**

Run: `git grep -n "verdicts_router\|adapter_events_ws"`
Expected: matches only in `redrobin/api/app.py`, the two module files themselves, and the four test files listed above. If anything else references them, STOP and report.

- [ ] **Step 2: Delete the modules and their tests**

```bash
git rm redrobin/api/verdicts_router.py redrobin/api/adapter_events_ws.py
git rm tests/integration/test_verdict_endpoint.py tests/integration/test_verdict_emission.py
git rm tests/integration/test_adapter_events_ws.py tests/integration/test_adapter_perms.py
git rm protocol/schemas/v0.1/events/opsec_verdict.schema.json
git rm protocol/fixtures/v0.1/valid/opsec_verdict.json protocol/fixtures/v0.1/invalid/opsec_verdict.json
```

Then drop `"opsec_verdict"` from the event-type list in `tests/unit/test_event_schemas.py` (it walks the schema + fixture set, so leaving the name in with the files gone turns that test red), and delete the `OpsecVerdictEvent` class from `redrobin/domain/events.py` along with the `Envelope.to_dict` branch that serializes its `Severity` field. **Keep the `Severity` enum itself** — the assistant needs it.

- [ ] **Step 3: Remove their registrations from `create_app`**

In `redrobin/api/app.py`, delete the two `import` lines for `verdicts_router` and `adapter_events_ws`, and the two lines in `create_app` that register them (an `app.include_router(...)` for the verdicts router and the router include for the adapter-events WebSocket). Leave every other router registration exactly as it is.

- [ ] **Step 4: Verify nothing dangles**

Run: `git grep -n "verdicts_router\|adapter_events_ws\|/v1/verdict/quick\|/v1/adapters/events"`
Expected: matches only in `adapters/cobaltstrike/redrobin.cna` (Task 7), docs (Task 8), and `docs/` history. No matches under `redrobin/` or `tests/`.

- [ ] **Step 5: Run the full suite + gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS, the suite smaller by the four deleted files' tests. No surviving test may fail — if one does, it depended on the gate's HTTP surface and that is a finding to report, not to patch.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(gate): delete the verdict HTTP endpoint and the adapter verdict WebSocket"
```

---

### Task 3: Delete the verdict pipeline, its factory, and the LessonPromoter

**Files:**
- Delete: `redrobin/application/verdict_pipeline.py` · `redrobin/application/verdict_pipeline_factory.py` · `redrobin/application/lesson_promoter.py`
- Modify: `redrobin/api/lifespan_helpers.py` (delete `start_pipeline` and `start_lesson_promoter` and their imports) · `redrobin/api/app.py` (drop both from the startup sequence and the shutdown block) · `redrobin/application/agents/assistant/tools.py` (line ~81 — a comment reads *"Mirrors verdict_pipeline_factory.build_default_dependencies"*; the referent is being deleted, so drop that sentence)
- Delete: `tests/unit/test_verdict_pipeline.py` · `tests/integration/test_verdict_pipeline_layer_b_beacon_id.py` · `tests/integration/test_verdict_pipeline_layer_b_command.py` · `tests/integration/test_lesson_promotion.py` · **`tests/e2e/test_retroactive_journey.py`** (the whole journey is Layer B's retroactive verdict correction — 28 gate references)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing. After this task the Layer A/B engines are unreferenced, which is what makes Task 4 safe.

**Why the promoter goes here:** its aggregation key is `(command, verdict)` and it only promotes when the verdict is `BLOCK`/`WARN` (`_ACTIONABLE_VERDICTS`). With no verdicts it has **no signal** — it loses its premise, not just its feed. The assistant's own `capture_lesson` (operator corrections → L1, scope-quarantined) already covers lessons, better.

- [ ] **Step 1: Delete the modules and their tests**

```bash
git rm redrobin/application/verdict_pipeline.py redrobin/application/verdict_pipeline_factory.py
git rm redrobin/application/lesson_promoter.py
git rm tests/unit/test_verdict_pipeline.py tests/integration/test_lesson_promotion.py
git rm tests/integration/test_verdict_pipeline_layer_b_beacon_id.py
git rm tests/integration/test_verdict_pipeline_layer_b_command.py
git rm tests/e2e/test_retroactive_journey.py
```

- [ ] **Step 2: Delete the two lifespan helpers**

In `redrobin/api/lifespan_helpers.py`, delete the entire `start_pipeline` function and the entire `start_lesson_promoter` function, plus these imports (they become unused):

```python
from redrobin.application.lesson_promoter import LessonPromoter
from redrobin.application.verdict_pipeline_factory import (
    build_default_dependencies,
    build_pipeline,
)
```

Leave `start_l5_summarizer` alone — L5 survives.

- [ ] **Step 3: Remove them from the startup sequence in `app.py`**

In `redrobin/api/app.py`, delete these two lines from the startup sequence:

```python
    _, _close_pipeline = await start_pipeline(app, settings)  # no-op close
    _, close_promoter = await start_lesson_promoter(app, settings)
```

and remove `start_lesson_promoter` and `start_pipeline` from the `lifespan_helpers` import list.

- [ ] **Step 4: Remove the promoter from the shutdown block in `app.py`**

Replace this block (step 3 of the shutdown sequence):

```python
        # 3. Cancel the promoter + summariser loops. Both run
        #    independently of the bus and reducer; cancelling them before
        #    ``bus.close()`` avoids a tail promotion/summarisation racing
        #    the shutdown.
        try:
            await close_promoter()
        except Exception:
            logger.exception("close_promoter failed")
        try:
            await close_summarizer()
        except Exception:
            logger.exception("close_summarizer failed")
```

with:

```python
        # 3. Cancel the summariser loop. It runs independently of the bus and
        #    reducer; cancelling it before ``bus.close()`` avoids a tail
        #    summarisation racing the shutdown.
        try:
            await close_summarizer()
        except Exception:
            logger.exception("close_summarizer failed")
```

- [ ] **Step 5: Fix the startup log line**

The startup `logger.info` in `app.py` reports `promoter_interval`. Remove the `promoter_interval=%.1fs` fragment from the format string and its corresponding argument, leaving the other fields untouched.

- [ ] **Step 6: Verify nothing dangles**

Run: `git grep -n "verdict_pipeline\|VerdictPipeline\|LessonPromoter\|lesson_promoter\|start_pipeline\|close_promoter\|promoter_interval"`
Expected: matches only in `docs/` (Task 8) and `redrobin/infrastructure/config.py` (the `promoter_*` Settings, which stay — they are harmless config that Task 8 may prune; do NOT remove them here). No matches in `redrobin/api/`, `redrobin/application/`, or `tests/`.

- [ ] **Step 7: Run the full suite + gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS, smaller again. Any surviving failure is a finding to report.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(gate): delete the verdict pipeline, its factory, and the lesson promoter"
```

---

### Task 4: Delete Layer A/B, their satellites, and the rule data

**Files:**
- Delete (application): `layer_a_engine.py` · `layer_b_engine.py` · `redactor.py` · `scope_enforcer.py` · `rules_lifecycle.py`
- Delete (domain): `redrobin/domain/opsec.py` · `redrobin/domain/rules.py`
- Delete (infrastructure): `redrobin/infrastructure/rules_loader.py`
- Delete (product data): every YAML under `rules/`
- Modify: `redrobin/api/lifespan_helpers.py` (delete `start_rules`; drop the `RulesLoader` / `ScopeEnforcer` imports) · `redrobin/api/app.py` (drop `start_rules` from startup and `stop_rules` from shutdown; fix the startup log) · `redrobin/infrastructure/boot_validation.py` (re-word the ANTHROPIC requirement) · `redrobin/cli_helpers/init_cmd.py` (drop the rule-seeding comment/behavior that references `RuleSeverity`)
- Delete (tests): `tests/unit/test_layer_a_engine.py` · `test_layer_b_engine.py` · `test_layer_b_engine_prompt.py` · `test_domain_rules.py` · `test_rules_loader.py` · `test_scope_enforcer.py` · `tests/integration/test_bootstrap_rules.py` · `test_layer_b_engagement_context.py` · `test_redaction.py` · `test_sigma_extended.py` · `test_engagement_scope.py` · `test_engagement_empty_scope_warn.py` · **`tests/e2e/test_sigma_real_rule_fires.py`** (a real SigmaHQ rule through `/v1/verdict/quick` — 52 gate references) · **`tests/e2e/test_hot_reload_journey.py`** (the rules hot-reload journey — 30 references)
- Modify (tests): **`tests/e2e/test_init_to_running_daemon.py`** — it asserts the rule-seeding/`rules_dir` part of `redrobin init` (21 gate references). Keep the init→daemon journey; delete only the assertions about seeded rules and the rules directory. **`tests/e2e/test_events_to_memory_journey.py`** and **`tests/e2e/test_memory_journey.py`** each carry a single gate reference — check and update it (they are L3/memory journeys, so if either drives episodes through `opsec.verdict` it must drive them through `beacon.command_input` instead, matching Task 1)
- Modify: `redrobin/application/agents/summarizer.py` — its module docstring says episodes come from `LayerBEngine`; re-word to `beacon.command_input` (the referent is deleted in this task)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing. `Severity` in `redrobin/domain/events.py` **survives** — only `VerdictAction` (`opsec.py`) and `RuleSeverity` (`rules.py`) are deleted.

**Note on `ScopeEnforcer`:** it is used only by the deleted pipeline plus `start_engagement`'s construction of it. The scope *data* survives — `engagement.yaml`'s `in_scope`/`out_of_scope` still project onto working memory via `apply_engagement_scope`, and the assistant still reads them. Only the *checker* goes.

- [ ] **Step 1: Confirm the deletion set is unreferenced outside itself**

Run: `git grep -n "layer_a_engine\|layer_b_engine\|LayerAEngine\|LayerBEngine\|Redactor\|ScopeEnforcer\|RulesLoader\|rules_lifecycle\|domain.opsec\|domain.rules\|VerdictAction\|RuleSeverity"`
Expected: matches only inside the files being deleted, in `redrobin/api/lifespan_helpers.py` + `app.py` (this task's modifications), `redrobin/cli_helpers/init_cmd.py` (a comment), `.claude/rules/testing.md` + docs (Task 8), and the deleted tests. If anything else appears — especially in `redrobin/application/agents/` — STOP and report.

- [ ] **Step 2: Delete the modules, the rule data, and the tests**

```bash
git rm redrobin/application/layer_a_engine.py redrobin/application/layer_b_engine.py
git rm redrobin/application/redactor.py redrobin/application/scope_enforcer.py
git rm redrobin/application/rules_lifecycle.py
git rm redrobin/domain/opsec.py redrobin/domain/rules.py
git rm redrobin/infrastructure/rules_loader.py
git rm -r rules/
git rm tests/unit/test_layer_a_engine.py tests/unit/test_layer_b_engine.py
git rm tests/unit/test_layer_b_engine_prompt.py tests/unit/test_domain_rules.py
git rm tests/unit/test_rules_loader.py tests/unit/test_scope_enforcer.py
git rm tests/integration/test_bootstrap_rules.py tests/integration/test_layer_b_engagement_context.py
git rm tests/integration/test_redaction.py tests/integration/test_sigma_extended.py
git rm tests/integration/test_engagement_scope.py tests/integration/test_engagement_empty_scope_warn.py
git rm tests/e2e/test_sigma_real_rule_fires.py tests/e2e/test_hot_reload_journey.py
```

- [ ] **Step 3: Delete `start_rules` and the ScopeEnforcer construction**

In `redrobin/api/lifespan_helpers.py`: delete the entire `start_rules` function; delete the `RulesLoader` and `ScopeEnforcer` imports; and in `start_engagement`, delete the two lines that build a `ScopeEnforcer` (the `in_scope=["*"]` degraded case and the engagement-backed case) plus any `app.state` assignment of it. Keep everything else in `start_engagement` — the engagement load and `app.state.engagement` are still needed.

- [ ] **Step 4: Remove `start_rules` from `app.py`**

Delete this line from the startup sequence:

```python
    _, stop_rules = await start_rules(app, settings)
```

Delete `start_rules` from the `lifespan_helpers` import list. Delete this block from the shutdown sequence:

```python
        # 6. Stop the rules-loader hot-reload watchdog last.
        try:
            await stop_rules()
        except Exception:
            logger.exception("stop_rules failed")
```

In the startup `logger.info`, remove the `rules_dir=%s rules=%d` fragments and their arguments, leaving the remaining fields intact.

- [ ] **Step 5: Re-word the boot validation**

In `redrobin/infrastructure/boot_validation.py`, the `ANTHROPIC_API_KEY` requirement stays but its rationale changes — Layer B no longer exists. Replace the docstring line and the two message strings so they read in terms of the assistant/enricher/summarizer:

```python
    - ANTHROPIC_API_KEY missing (the assistant, enricher, and summariser cannot call the model)
```

```python
                "ANTHROPIC_API_KEY missing — running degraded (no assistant, enrichment, or summaries)"
```

```python
                "ANTHROPIC_API_KEY required for the assistant/enricher/summariser. Set it, or pass "
                "--allow-degraded / REDROBIN_ALLOW_DEGRADED=1 to run degraded."
```

Leave the `VOYAGE_API_KEY` branch untouched.

- [ ] **Step 6: Clean the CLI's rule seeding**

In `redrobin/cli_helpers/init_cmd.py`, remove the comment block that explains `RuleSeverity`/`SEVERITY_TO_ACTION` and any code that seeds starter rule YAMLs into `rules/`. If `init` no longer needs a rules directory at all, drop that step from the command; if it still creates the directory as part of the standard layout, leave the directory creation and remove only the rule seeding.

- [ ] **Step 7: Verify nothing dangles and that `Severity` survived**

Run: `git grep -n "VerdictAction\|RuleSeverity\|layer_a\|layer_b\|Redactor\|ScopeEnforcer\|RulesLoader"`
Expected: matches only in `docs/`, `.claude/rules/*.md` (Task 8), and `adapters/` (Task 7). Nothing under `redrobin/` or `tests/`.

Run: `git grep -n "from redrobin.domain.events import Severity"`
Expected: still present in `redrobin/application/agents/assistant/parser.py` and `redrobin/domain/assistant.py` — **`Severity` must survive.**

- [ ] **Step 8: Run the full suite + gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS, substantially smaller. **The advisory-rendering tests must be among the survivors** (`test_format_recommendation_*` in `tests/unit/tui_textual/test_chat.py`) — they are the guard that `Severity` was not deleted by accident.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(gate): delete Layer A/B, the scope enforcer, the redactor, and the Sigma rule pack"
```

---

### Task 5: Narrow the memory types to command activity

**Files:**
- Modify: `redrobin/domain/memory.py` (`Episode`: drop `verdict` and `reason`) · `redrobin/infrastructure/persistence/episodic_repo.py` (`record` signature, the INSERT, the schema, the row mapping) · `redrobin/application/agents/summarizer.py` (`_format`) · `redrobin/application/working_memory_reducer.py` (`_on_verdict` is already gone with the dispatch branch — remove the now-dead `verdict=""`/`reason=""` arguments from `_on_command_input`) · `redrobin/application/dedup_keys.py` (`_CORRELATED_TYPES`)
- Test: `tests/unit/test_reducer_command_episodes.py` (update) · `tests/unit/test_summarizer.py` (update `_format` expectations) — plus a new `_format` test

**Interfaces:**
- Consumes: `_on_command_input` from Task 1.
- Produces: `Episode(id, engagement_id, correlation_id, command, created_at)` · `EpisodicRepo.record(*, engagement_id, correlation_id, command, created_at=None) -> Episode`.

**Why now and not earlier:** the fields disappear only once nothing writes them. Tasks 2-4 removed the verdict path, so this is the first point at which narrowing cannot break a live writer.

- [ ] **Step 1: Confirm nothing still writes a verdict episode**

Run: `git grep -n "_on_verdict\|verdict=\|\.verdict"`
Expected: no matches under `redrobin/` except `Episode.verdict`'s own definition and the repo's INSERT/mapping — i.e. only the code this task is about to change. If a writer remains, STOP and report.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_reducer_command_episodes.py`:

```python
def test_episode_has_no_verdict_or_reason_fields():
    import dataclasses

    from redrobin.domain.memory import Episode

    names = {f.name for f in dataclasses.fields(Episode)}
    assert names == {"id", "engagement_id", "correlation_id", "command", "created_at"}
```

Create `tests/unit/test_summarizer_format.py`:

```python
from __future__ import annotations

from datetime import datetime

from redrobin.application.agents.summarizer import L5Summarizer
from redrobin.domain.memory import Episode


def test_format_renders_a_command_sequence():
    episodes = [
        Episode(
            id=1,
            engagement_id="eng-1",
            correlation_id="c-1",
            command="whoami",
            created_at=datetime(2026, 7, 25, 12, 0, 0),
        ),
        Episode(
            id=2,
            engagement_id="eng-1",
            correlation_id="c-2",
            command="type db_backup.config",
            created_at=datetime(2026, 7, 25, 12, 0, 5),
        ),
    ]
    out = L5Summarizer._format(episodes)
    assert "- 2026-07-25T12:00:00 whoami" in out
    assert "- 2026-07-25T12:00:05 type db_backup.config" in out
    assert "[" not in out.split("\n", 1)[1]  # no leftover verdict bracket in the episode lines
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_summarizer_format.py tests/unit/test_reducer_command_episodes.py -q`
Expected: FAIL — `Episode` still has `verdict`/`reason`, so the field-set assertion fails and constructing an `Episode` without them raises `TypeError`.

- [ ] **Step 4: Narrow the `Episode` dataclass**

In `redrobin/domain/memory.py`, replace the `Episode` class with:

```python
@dataclass(frozen=True)
class Episode:
    """One command the operator ran — the raw activity log L5 narrates from.

    Sourced from ``beacon.command_input`` by the reducer. Before the pre-flight gate was
    removed this was one row per ``opsec.verdict``; the gate fired on every command, so the
    verdict stream was a de-facto command stream. The command stream is the real one.
    """

    id: int | None  # None until persisted
    engagement_id: str
    correlation_id: str
    command: str
    created_at: datetime
```

- [ ] **Step 5: Narrow the repo**

In `redrobin/infrastructure/persistence/episodic_repo.py`:

Change the `episodes` table in the schema SQL to drop the two columns:

```sql
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id VARCHAR(128) NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    command TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

(`CREATE TABLE IF NOT EXISTS` means an existing `episodic.db` keeps its old columns; they are simply never written or read again. No migration — engagements are per-directory and short-lived.)

Replace `record` with:

```python
    def record(
        self,
        *,
        engagement_id: str,
        correlation_id: str,
        command: str,
        created_at: datetime | None = None,
    ) -> Episode:
        """Append one command-activity episode (synchronous, file-backed)."""
        ts = created_at or datetime.now()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO episodes (engagement_id, correlation_id, command, "
                "created_at) VALUES (?, ?, ?, ?)",
                (engagement_id, correlation_id, command, _iso(ts)),
            )
            episode_id = cursor.lastrowid
        return Episode(
            id=episode_id,
            engagement_id=engagement_id,
            correlation_id=correlation_id,
            command=command,
            created_at=ts,
        )
```

Then update `list_episodes_for_engagement` to select and map only the five surviving columns (drop `verdict` and `reason` from both the SELECT list and the `Episode(...)` construction).

- [ ] **Step 6: Drop the dead arguments from the reducer**

In `redrobin/application/working_memory_reducer.py`, `_on_command_input` currently passes `verdict=""` and `reason=""`. Remove those two arguments so the call matches the narrowed signature:

```python
            self._episodic.record(
                engagement_id=self._engagement_id,
                correlation_id=str(payload.get("correlation_id") or ""),
                command=command,
            )
```

Also update the module docstring's event dispatch table: replace the `opsec.verdict` line with `beacon.command_input → record one Episode (L3 activity log; no working-memory mutation)`.

- [ ] **Step 7: Narrow the summariser's formatter**

In `redrobin/application/agents/summarizer.py`, replace `_format` with:

```python
    @staticmethod
    def _format(episodes: list[Episode]) -> str:
        lines = [f"- {e.created_at.isoformat()} {e.command}" for e in episodes]
        return _DEFAULT_PROMPT_HEADER + "\n".join(lines)
```

If `_DEFAULT_PROMPT_HEADER` describes verdicts, re-word it to describe a command sequence instead.

- [ ] **Step 8: Prune the dedup key set**

In `redrobin/application/dedup_keys.py`, drop `"opsec.verdict"` from `_CORRELATED_TYPES`:

```python
_CORRELATED_TYPES = frozenset({"beacon.command_input", "beacon.command_output"})
```

and update the docstring/comment that enumerates the three types (including the note that "opsec.verdict uses `beacon_id`; the beacon.* events use `bid`" — with the verdict gone, only `bid` remains).

- [ ] **Step 9: Update the existing summariser tests**

Any surviving test that constructs an `Episode` with `verdict=`/`reason=` must be updated to the narrowed constructor. Run `git grep -n "Episode(" tests/` to find them, and change only the constructor arguments — do not change what a test asserts about summarisation behavior.

- [ ] **Step 10: Run the tests to verify they pass, then the full suite + gates**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_summarizer_format.py tests/unit/test_reducer_command_episodes.py -q`
Expected: PASS.

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy redrobin/domain redrobin/application && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor(memory): narrow Episode to command activity and prune the verdict dedup key"
```

---

### Task 6: Remove the gate's TUI surface

**Files:**
- Modify: `redrobin/tui/textual_ui/panels.py` (delete `format_verdict`, `apply_verdict`, `verdict_counts`, `VERDICT_COLORS`, `_MAX_EVENTS`; narrow `format_topbar` and `TopBar.update_status`) · `redrobin/tui/textual_ui/app.py` (delete the `events` reactive, `watch_events`, the `opsec.verdict` handler, and the counts argument) · `redrobin/tui/textual_ui/theme.tcss` (delete the four `$verdict-*` tokens)
- Test: `tests/unit/tui_textual/test_panels.py` (update) · `tests/unit/tui_textual/test_app.py` (update) · **`tests/e2e/test_tui_daemon_journey.py`** (update — it has 14 gate references, almost certainly asserting the topbar's verdict counters and/or driving an `opsec.verdict` frame; keep the journey, drop those assertions) — plus a new topbar test

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `format_topbar(engagement: str, connection_state: str) -> Text` · `TopBar.update_status(engagement: str, connection_state: str) -> None` — both lose the `counts` parameter.

**Operator decision (spec §5.5): the freed topbar space stays EMPTY.** The header becomes `Red Robin · <engagement> · ● connected`. Do not add a phase indicator, progress, or an assistant-state widget — that is separate UX work.

**⚠️ Do NOT touch `redrobin/tui/textual_ui/chat.py`.** Its `_RISK_COLORS` maps the assistant's `Severity` values (INFO/WARN/DANGER/ESCALATE) and is unrelated to the gate's `VerdictAction`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/tui_textual/test_panels.py`:

```python
def test_topbar_has_no_verdict_counters():
    from redrobin.tui.textual_ui.panels import format_topbar

    s = format_topbar("vantage", "connected").plain
    assert "Red Robin" in s
    assert "vantage" in s
    assert "connected" in s
    for word in ("BLOCK", "PASS", "ESCALATE"):
        assert word not in s
```

Add to `tests/unit/tui_textual/test_app.py`:

```python
@pytest.mark.asyncio
async def test_app_has_no_events_reactive_and_no_verdict_handler():
    from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp

    assert not REGISTRY.has_handler("opsec.verdict")
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        assert not hasattr(app, "events")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py tests/unit/tui_textual/test_app.py -q`
Expected: FAIL — `format_topbar` still requires a third `counts` argument (TypeError), and the registry still has the `opsec.verdict` handler.

- [ ] **Step 3: Narrow the topbar**

In `redrobin/tui/textual_ui/panels.py`, replace `format_topbar` and `TopBar` with:

```python
def format_topbar(engagement: str, connection_state: str) -> Text:
    """Header line: 'Red Robin · <engagement> · ●<connection>'.

    16-color-safe: the connection state carries a text label alongside the dot, so
    meaning survives non-truecolor terminals and colour-blindness."""
    line = Text()
    line.append("Red Robin", style="bold")
    line.append(f"  ·  {engagement or '-'}  ·  ", style=_MUTED)
    line.append("●", style=_STATUS_COLORS.get(connection_state, _MUTED))
    line.append(f" {connection_state}", style=_MUTED)
    return line
```

```python
class TopBar(Static):
    """Docked header: engagement id + daemon-connection status."""

    def update_status(self, engagement: str, connection_state: str) -> None:
        self.update(format_topbar(engagement, connection_state))
```

Then delete `format_verdict`, `apply_verdict`, `verdict_counts`, the `VERDICT_COLORS` dict, and `_MAX_EVENTS` (used only by `apply_verdict`), and remove all four from `__all__` if listed.

- [ ] **Step 4: Remove the events machinery from the app**

In `redrobin/tui/textual_ui/app.py`:
- delete the `events: reactive[list] = reactive(list)` declaration;
- delete the entire `watch_events` method;
- delete the `@REGISTRY.register("opsec.verdict")` handler function;
- remove `apply_verdict` and `verdict_counts` from the `panels` import;
- in `_refresh_topbar`, drop the counts argument so the call reads:

```python
        bar.update_status(engagement, self.connection_state)
```

- [ ] **Step 5: Remove the CSS tokens**

In `redrobin/tui/textual_ui/theme.tcss`, delete these four lines:

```css
$verdict-block: #f85149;
$verdict-warn: #e3b341;
$verdict-pass: #3fb950;
$verdict-escalate: #58a6ff;
```

If any surviving rule references one of them, re-point it at the equivalent `$status-*` token rather than re-adding the deleted one.

- [ ] **Step 6: Update the tests that exercised the deleted helpers**

Run `git grep -n "apply_verdict\|verdict_counts\|format_verdict\|app.events" tests/` and delete the tests whose entire subject was one of those helpers. Do **not** weaken a test that covers something else; if a test covers both, keep the surviving half.

- [ ] **Step 7: Run the tests to verify they pass, then the full suite + gates**

Run: `uv run --no-sync pytest tests/unit/tui_textual/ -q`
Expected: PASS.

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS. **The advisory-rendering tests must still be green** — they prove `Severity` and `chat.py` were left alone.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync lint-imports --config importlinter.cfg`
Expected: clean; `Contracts: 4 kept, 0 broken.`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(tui): drop the verdict counters and the events reactive from the topbar"
```

---

### Task 7: Clean the `.cna` (operator-smoke acceptance)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. The capture path is unchanged.

**Verified state of the file (2026-07-25):** the gate is **already toothless**. The five hooks are `set BEACON_OUTPUT`, `on beacon_input`, `on beacon_initial`, `on heartbeat_5s`, `on script_unload`. There is **no `alias` override anywhere**, and `on beacon_input` only emits `beacon.command_input` — it never calls the gate. `redrobin_gate` and `redrobin_query_quick` are **dead code: defined, never called**. So this task deletes dead code plus one console-message branch; it does **not** touch the operator's command path.

**⚠️ Do NOT modify `on beacon_input`, `on beacon_initial`, or `set BEACON_OUTPUT`.** They are the capture path the assistant depends on.

- [ ] **Step 1: Confirm the gate subs are unreferenced**

Run: `grep -n "redrobin_gate\|redrobin_query_quick" adapters/cobaltstrike/redrobin.cna`
Expected: exactly two matches — the `sub redrobin_gate {` and `sub redrobin_query_quick {` definitions, with **no call sites**. If a call site exists, STOP and report: the risk profile of this task changes completely.

- [ ] **Step 2: Delete the two dead subs**

Delete the entire `sub redrobin_query_quick { … }` and `sub redrobin_gate { … }` blocks, including their leading comments.

- [ ] **Step 3: Remove the verdict console message from the heartbeat**

In `on heartbeat_5s`, delete the branch that polls `/v1/adapters/events` and prints Layer B verdicts — the code building `$msg = "[Red Robin] Layer B " . [$evt verdict] . ": " . [$evt reason];` and its `blog(...)` call, plus the surrounding loop/fetch that exists only to serve it. If the heartbeat has no other purpose after this, delete the whole `on heartbeat_5s` hook; if it also does something else (e.g. a health check), keep that part.

- [ ] **Step 4: Update the handshake declaration**

In `redrobin_handshake_with_retry`, the registration JSON declares `"events_consumed":["opsec.verdict"]`. Change it to an empty list — the adapter no longer consumes any event:

```
"events_consumed":[],
```

Leave `events_produced` exactly as it is.

- [ ] **Step 5: Verify what remains**

Run: `grep -nE "verdict|Layer B|/v1/(verdict|adapters/events)" adapters/cobaltstrike/redrobin.cna`
Expected: no matches.

Run: `grep -cE "^sub |^on |^set " adapters/cobaltstrike/redrobin.cna`
Expected: the capture hooks and helpers remain — `set BEACON_OUTPUT`, `on beacon_input`, `on beacon_initial`, `on script_unload`, plus the emit/http/escape helpers.

- [ ] **Step 6: Run the adapter tests**

Run: `uv run --no-sync pytest adapters/cobaltstrike/tests/ -q`
Expected: PASS. (Adapter tests are **not** part of CI's `pytest tests/` — they must be run explicitly whenever the adapter changes.)

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q`
Expected: PASS, unchanged from Task 6.

- [ ] **Step 7: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): delete the dead gate subs and the Layer B console message"
```

**Acceptance is a live operator smoke, not CI** — see the plan's Final section.

---

### Task 8: Update the documentation

**Files:**
- Modify: `CLAUDE.md` · `CONTRIBUTING.md` · `.claude/rules/wire-protocol.md` · `.claude/rules/testing.md` · `.claude/rules/architecture.md` · `docs/architecture-design.md` · `scripts/smoke/rr-assistant-smoke-runbook.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: `CLAUDE.md`**

Its opening describes the OPSEC pre-flight gate as *"the first feature and the capture hook that feeds the assistant"*. Re-word so the assistant is the product and the adapter is the capture boundary: the gate is gone; the `.cna` capture (`beacon.command_input` / `command_output` / `beacon_initial`) is what feeds the assistant. Also remove the top-level `rules/` explanation from the "Two unrelated rules dirs" note — only `.claude/rules/` remains.

- [ ] **Step 2: `CONTRIBUTING.md`**

Delete the entire **"Adding a rule"** section (it documents `rules/shared/<rule_id>.yaml`, the `match.command`/`severity` schema, and `tests/integration/test_bootstrap_rules.py` — all deleted). Leave "Adding an adapter" and everything else.

- [ ] **Step 3: `.claude/rules/wire-protocol.md`**

Delete the `opsec.verdict` row from the event table. If the file documents the two-tier verdict protocol or `/v1/verdict/quick`, delete those parts too. Leave every `beacon.*`, `operator.*`, and `engagement.milestone` entry.

- [ ] **Step 4: `.claude/rules/testing.md`**

Line 12-13 read:

```markdown
- **Canonical enums in assertions**: `VerdictAction` = PASS/WARN/BLOCK/ESCALATE (**no `ALLOW`**);
  `Severity` = INFO/WARN/DANGER/ESCALATE.
```

Replace with just the surviving enum:

```markdown
- **Canonical enum in assertions**: `Severity` = INFO/WARN/DANGER/ESCALATE (the assistant's
  `Recommendation.opsec_risk`).
```

- [ ] **Step 5: `.claude/rules/architecture.md`**

Update the facade list — `rules_lifecycle` is deleted, so remove it from the enumerated `application/*_lifecycle.py` facades. Leave the four contracts and everything else unchanged.

- [ ] **Step 6: `docs/architecture-design.md`**

Do **not** rewrite it — it is a v1.2 historical design doc. Add a short status note near the top of §Componente 3 (the memory table) and near the gate's own section recording that: the synchronous pre-flight gate (Layer A/B, verdicts) was removed on 2026-07-25; Red Robin is advisory-only; L3 Episodic is now sourced from `beacon.command_input` rather than `opsec.verdict`; L4 remains unbuilt. A full rewrite is its own effort.

- [ ] **Step 7: The smoke runbook**

In `scripts/smoke/rr-assistant-smoke-runbook.md`, delete the checkpoints that exercise the gate (any that expect a WARN/BLOCK/ESCALATE verdict or a console message from Layer B), and add the gate-removal checkpoint from this plan's Final section. Leave the assistant checkpoints (A-S) intact except where they reference a verdict.

- [ ] **Step 8: Verify no stale references remain**

Run: `git grep -n "Layer A\|Layer B\|VerdictAction\|opsec.verdict\|verdict/quick\|Sigma" -- "*.md" ":!docs/superpowers/*" ":!docs/architecture-design.md"`
Expected: no matches. (The `docs/superpowers/` specs and plans are historical records and keep their references; `architecture-design.md` keeps its history plus the new status note.)

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "docs: remove the pre-flight gate from the project documentation"
```

---

### Final: verification + acceptance

- [ ] **Step 1: Run all 5 CI gates one last time**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
uv run --no-sync pytest adapters/cobaltstrike/tests/ -q
```
Expected: all green; `Contracts: 4 kept, 0 broken.` Record the final test count and the delta from the branch point.

- [ ] **Step 2: Absence audit**

```bash
git grep -n "VerdictAction\|RuleSeverity\|LayerAEngine\|LayerBEngine\|verdict_pipeline\|LessonPromoter\|opsec.verdict\|opsec_verdict\|ScopeEnforcer\|RulesLoader\|Redactor" -- redrobin/ tests/ adapters/ protocol/
```
Expected: **no matches at all.** Any hit is dead code or a missed dependency.

Note this audit covers **`protocol/` too**, and it catches *comments*, not just imports — two docstrings referenced deleted symbols before this plan (`summarizer.py`'s `LayerBEngine`, `tools.py`'s `verdict_pipeline_factory`) and are fixed in Tasks 4 and 3 respectively. If either survives, this step fails.

```bash
git grep -n "from redrobin.domain.events import Severity" -- redrobin/
```
Expected: still present — `Severity` survives for the assistant.

- [ ] **Step 3: Degraded-boot check (proves L5's input no longer needs an LLM)**

Start the daemon with no `ANTHROPIC_API_KEY` (`REDROBIN_ALLOW_DEGRADED=1`), publish a `beacon.command_input` event to `POST /v1/events`, and confirm a row lands in the engagement's `episodic.db`. This is the concrete proof that the L3 re-source is not LLM-dependent — the property the old deterministic Layer A provided.

- [ ] **Step 4: Whole-change review** (subagent-driven-development dispatches the final reviewer here).

- [ ] **Step 5: Operator live smoke — the acceptance for the `.cna` (CI cannot run it)**

With a live Cobalt Strike beacon on the lab engagement:
1. Run a command the gate used to flag as DANGER (`mimikatz sekurlsa::logonpasswords`). **Expect: it dispatches immediately, with no interception and no `[Red Robin]` console message.**
2. Run a command that produces real output (e.g. `shell type` on the fixture config). **Expect: the assistant still advises in the TUI chat, and the Working-Memory panel still fills** (findings/credentials/hosts) — proving the capture → enricher → assistant path survived.
3. Check the topbar reads `Red Robin · <engagement> · ● connected` with **no verdict counters**.
4. After several commands, confirm `episodic.db` has one row per command and that an L5 summary eventually appears (it needs `assistant_l5_window` episodes).

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:

| Spec section | Task |
|---|---|
| §2 the accidental L3/verdict coupling | Task 1 (re-source) + Task 5 (narrow) |
| §5.1 deleted application modules (8) | Tasks 3 (pipeline, factory, promoter) + 4 (layer_a/b, redactor, scope_enforcer, rules_lifecycle) |
| §5.1 deleted API surfaces (2) | Task 2 |
| §5.1 deleted infrastructure (`rules_loader`) | Task 4 |
| §5.1 deleted product data (`rules/`) | Task 4 |
| §5.1 deleted domain (`opsec.py`, `rules.py`, `OpsecVerdictEvent`, `PromotedLesson`, `Episode` fields) | Task 4 (`opsec.py`, `rules.py`) + Task 5 (`Episode` fields) |
| §5.1 deleted `.cna` parts | Task 7 |
| §5.1 deleted tests (18 files) | Tasks 2 (4), 3 (4), 4 (12) — the spec's "18" counts the gate's own tests; Tasks 5-6 additionally prune helper-specific TUI/summariser tests |
| §5.2 what survives (capture → assistant) | Guarded by the Global Constraints + every task's full-suite step |
| §5.3 the L3/L5 re-source | Tasks 1 + 5 |
| §5.4 dedup_keys, lifespan/app, boot_validation | Task 5 (dedup) + Tasks 3-4 (lifespan/app, boot_validation) |
| §5.5 the TUI + the empty-topbar decision + the enum trap | Task 6 + the Global Constraints table |
| §6 non-goals | Nothing in the plan adds a replacement gate, redaction, a migration, or a flag |
| §7 ordering | Task 1 first; Task 5 (narrowing) after Tasks 2-4 |
| §8 DoD | Final steps 1-5 |
| §9 test strategy | Task 1 (reducer tests), Task 5 (`_format`, field-set), Task 6 (topbar/app), Final step 2 (absence audit), Final step 3 (degraded boot), Final step 5 (live smoke) |
| §10 risks | The `.cna` risk is addressed by Task 7 Step 1's stop-condition; the lost console message is checkpoint 1 of the smoke |

**2. Placeholder scan** — no `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Every modification shows its exact replacement code; every deletion names its exact files; every command states its expected output. Where a file's exact remaining content depends on something only visible at implementation time (the `init_cmd` rule seeding, the heartbeat's non-verdict content, `_DEFAULT_PROMPT_HEADER`'s wording), the step states the decision rule and a stop-condition rather than guessing.

**3. Type consistency** — `Episode(id, engagement_id, correlation_id, command, created_at)` is identical in Tasks 1 (via the repo call), 5 (the definition), and the Final audit. `EpisodicRepo.record` is called with `verdict=""`/`reason=""` in Task 1 and those arguments are explicitly removed in Task 5 Step 6 — the only intentional cross-task signature change, called out in both places. `format_topbar(engagement, connection_state)` and `TopBar.update_status(engagement, connection_state)` match between Task 6 Step 3 and Step 4's call site. `Severity` is named as KEPT in the Global Constraints, Task 4 Step 7, Task 6's warning, and the Final audit.

**4. Ordering safety** — each task ends with a green full suite, and the deletion runs top-down through the import graph (surfaces → pipeline → engines → domain), so no commit leaves a dangling import. Task 1's additive double-write is removed as a side effect of Tasks 2-4 rather than needing its own cleanup.
