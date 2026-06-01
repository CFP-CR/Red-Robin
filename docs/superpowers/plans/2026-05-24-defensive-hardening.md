# Plan A — v0.1.x defensive hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with Opus 4.7 per the operator's durable directive. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three independently-scoped defensive-coding gaps surfaced during prior code reviews and smoke sessions: `#125` (RedRobinEventStream resource leak on .cna unload), `#127` (WS endpoints' `bus.subscribe()` exception path leaves `queue` undefined), `#137` (daemon crashes hard on malformed `engagement.yaml`). All three are pre-existing latent issues that don't surface in normal operation but represent production-grade hygiene gaps the codebase should close before v0.1 ship.

**Architecture:** Three independent fixes covering three logical changes. The fixes touch four different files (`.cna`, two Python WS endpoints, Python lifespan helper) with no cross-dependencies, so they execute as serial tasks in a single subagent-driven plan. **Final commit count: 4** — T1 (#137 test RED) + T2 (#137 fix GREEN) + T3 (#127 fix) + T4 (#125 fix). T1+T2 is the standard TDD red/green pair, counting as one logical change for #137. T5 is operator smoke (no commit).

**Tech Stack:** Sleep/Aggressor (`.cna` for #125), FastAPI WebSocket endpoints (Python for #127), pydantic-settings + yaml (Python for #137), pytest (regression coverage for #137).

**Verified facts driving this plan (no assumptions):**

1. **#125 surface verified:** `redrobin.cna` has no `on script_unload` hook (verified via `grep "on script_unload" adapters/cobaltstrike/redrobin.cna` returning empty during 2026-05-20 T11 code review). `RedRobinEventStream.close()` already exists in the JAR at `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinEventStream.java:64-71` (added by Task 9 of the adapter-event-stream plan). The hook just needs to call it.

2. **#127 surface verified:** `redrobin/api/tui_ws.py:27` reads `queue = await bus.subscribe()` AFTER `await websocket.accept()` but outside any `try`. The `finally: await bus.unsubscribe(queue)` at line 43 will `UnboundLocalError` if `subscribe()` raises. Same pattern at `redrobin/api/adapter_events_ws.py:31` and `:45`. Verified by reading both files in full.

3. **#137 surface verified:** `redrobin/api/lifespan_helpers.py:start_engagement` (lines 181-222 in current HEAD `1ab46b9`) has `try: EngagementLoader(...).load(slug) except FileNotFoundError`. No other exception class is caught — verified during B2 code review 2026-05-24. `yaml.YAMLError`, `OSError`, `UnicodeDecodeError` all propagate and would crash uvicorn during application startup.

**Risk note:** Each fix is small (~5-15 LOC). The main risk is breaking existing behavior — mitigated by:
- #125: `close()` is a no-op when `$REDROBIN_EVENT_STREAM` is null (we add a `!is $null` guard).
- #127: `queue = None` + `if queue is not None` is purely additive — doesn't change the happy path.
- #137: broader exception catch adds new code paths but the existing FileNotFoundError handler stays. New regression test prevents future drift.

**Starting state assumption:** `dev-juan` HEAD = `1ab46b9` (backlog reorg). All critical-for-demo bugs (#133, #134, #138) shipped. JAR at 1.0.2.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `adapters/cobaltstrike/redrobin.cna` | Modify | Add `on script_unload` hook calling `[$REDROBIN_EVENT_STREAM close]` (#125) |
| `redrobin/api/tui_ws.py` | Modify | Initialize `queue = None`, guard `unsubscribe` (#127) |
| `redrobin/api/adapter_events_ws.py` | Modify | Same guard pattern (#127) |
| `redrobin/api/lifespan_helpers.py` | Modify | Broaden `start_engagement` except clause (#137) |
| `tests/integration/test_engagement_malformed_yaml.py` | Create | Regression test for #137 |

---

## Task 1: TDD red — pytest for malformed `engagement.yaml` survival (#137)

**Files:**
- Create: `tests/integration/test_engagement_malformed_yaml.py`

This is the TDD red phase. Write a test that loads a daemon with a malformed engagement.yaml and asserts the daemon survives in degraded (wildcard scope) mode rather than crashing.

- [ ] **Step 1: Write the failing pytest**

Create `tests/integration/test_engagement_malformed_yaml.py` with EXACTLY this content:

```python
"""Daemon must survive malformed `engagement.yaml` by falling back to wildcard scope.

Pre-existing: lifespan_helpers.py:start_engagement catches only FileNotFoundError.
A malformed YAML, PermissionError, or UnicodeDecodeError crashes uvicorn at
startup with a Python traceback. This test pins the contract that those error
classes also fall through to the wildcard-scope degraded mode (consistent with
the FileNotFoundError handler).

References:
  - lifespan_helpers.py:start_engagement (current FileNotFoundError-only catch)
  - backlog.md #137 (defensive hardening — broaden exception handler)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.application.engagement_service import EngagementService


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_ID", raising=False)
    yield tmp_path


def test_malformed_yaml_falls_back_to_wildcard_scope(isolated_root):
    """Active engagement with malformed engagement.yaml → daemon boots, scope is wildcard."""
    svc = EngagementService(root=isolated_root)
    svc.new("bad-yaml-engagement")
    svc.use("bad-yaml-engagement")

    # Corrupt the engagement.yaml — leave the file present but with invalid YAML
    eng_yaml = isolated_root / "engagements" / "bad-yaml-engagement" / "engagement.yaml"
    eng_yaml.write_text("this: is: not: valid: yaml: at: all: [unclosed\n")

    # Daemon must boot — no crash from yaml.YAMLError propagation
    app = create_app()
    with TestClient(app) as _client:
        # Engagement load failed silently; app.state.engagement is None,
        # scope falls back to wildcard. app.state.engagement_id may still
        # carry the slug from start_working_memory (which reads it from
        # .active_engagement); that's acceptable — the contract is "don't
        # crash", not "scrub state".
        assert app.state.engagement is None, (
            "malformed YAML must yield engagement=None (wildcard scope), "
            "not propagate a crash"
        )
        # Wildcard scope is in_scope=['*'], out_of_scope=[]
        assert app.state.scope.in_scope == ["*"]


def test_unreadable_yaml_falls_back_to_wildcard_scope(isolated_root):
    """engagement.yaml present but unreadable (encoding error) → wildcard scope, no crash."""
    svc = EngagementService(root=isolated_root)
    svc.new("bad-encoding-engagement")
    svc.use("bad-encoding-engagement")

    # Write bytes that aren't valid UTF-8
    eng_yaml = isolated_root / "engagements" / "bad-encoding-engagement" / "engagement.yaml"
    eng_yaml.write_bytes(b"\xff\xfe\x00\x00 not utf-8 \x80\x81\x82")

    app = create_app()
    with TestClient(app) as _client:
        assert app.state.engagement is None
        assert app.state.scope.in_scope == ["*"]
```

- [ ] **Step 2: Run pytest to verify RED**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
REDROBIN_ALLOW_DEGRADED=1 .venv/Scripts/pytest.exe tests/integration/test_engagement_malformed_yaml.py -v
```

Expected: BOTH tests fail with `yaml.YAMLError` or `UnicodeDecodeError` (or similar) propagating from `EngagementLoader.load()` during the `with TestClient(app) as client:` lifespan startup. The test scaffolding (fixture + assertions) is correct — it's the production code that needs to catch the exceptions.

If only one of the two tests fails (e.g., one mode catches and one doesn't), STOP and report — we may have an inconsistent existing catch.

- [ ] **Step 3: Commit (RED state)**

```bash
git add tests/integration/test_engagement_malformed_yaml.py
git commit -m "test(daemon): assert start_engagement survives malformed YAML (TDD red, #137)

Two pytest cases pin the defensive contract: when engagement.yaml exists
but is unparseable (yaml.YAMLError) or unreadable (UnicodeDecodeError),
the daemon falls back to wildcard scope rather than crashing uvicorn at
startup with a Python traceback.

Both tests FAIL until Task 2 broadens the except clause in
lifespan_helpers.py:start_engagement. Today only FileNotFoundError is
caught; other exception classes propagate.

Spec: docs/superpowers/plans/2026-05-24-defensive-hardening.md Task 1
Backlog ref: docs/superpowers/backlog.md #137"
```

---

## Task 2: TDD green — broaden `start_engagement` exception handling (#137)

**Files:**
- Modify: `redrobin/api/lifespan_helpers.py:start_engagement`

- [ ] **Step 1: Read current start_engagement**

```bash
grep -n "start_engagement\|FileNotFoundError" redrobin/api/lifespan_helpers.py | head -10
```

Locate the existing `try/except FileNotFoundError` block (around line 197-206 in current HEAD).

- [ ] **Step 2: Broaden the exception catch**

Find this block (in `start_engagement`, around lines 196-206):

```python
    if engagement_slug:
        try:
            engagement = EngagementLoader(engagements_dir).load(engagement_slug)
        except FileNotFoundError:
            logger.warning(
                "engagement slug %s set but file not found under %s — "
                "falling back to wildcard scope",
                engagement_slug,
                engagements_dir,
            )
            engagement = None
```

Replace with:

```python
    if engagement_slug:
        try:
            engagement = EngagementLoader(engagements_dir).load(engagement_slug)
        except FileNotFoundError:
            logger.warning(
                "engagement slug %s set but file not found under %s — "
                "falling back to wildcard scope",
                engagement_slug,
                engagements_dir,
            )
            engagement = None
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            # Broadened catch (#137) — malformed YAML, permission errors,
            # encoding issues should NOT crash uvicorn at startup. Fall
            # back to wildcard scope and warn loudly so the operator sees
            # the problem in the banner / logs without losing daemon
            # availability. FileNotFoundError stays a separate case above
            # because its log message is specific to "file missing".
            logger.warning(
                "engagement slug %s present but engagement.yaml is "
                "unreadable / malformed (%s: %s) — falling back to "
                "wildcard scope. Fix the YAML and restart to recover.",
                engagement_slug,
                type(exc).__name__,
                exc,
            )
            engagement = None
```

Add the yaml import at the top of the file if it's not already imported (verify with `grep "^import yaml\|^from yaml" redrobin/api/lifespan_helpers.py`). If not present, add `import yaml  # type: ignore[import-untyped]` alongside the other top-level imports.

- [ ] **Step 3: Run the Task 1 pytest to verify GREEN**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
REDROBIN_ALLOW_DEGRADED=1 .venv/Scripts/pytest.exe tests/integration/test_engagement_malformed_yaml.py -v
```

Expected: both tests PASS.

- [ ] **Step 4: Run broader integration suite to check no regressions**

```bash
REDROBIN_ALLOW_DEGRADED=1 .venv/Scripts/pytest.exe tests/integration/ -v 2>&1 | tail -10
```

Expected: same pass count as before plus the 2 new tests (so ~187 passed, 3 skipped — pre-existing baseline + 2 from Task 1). 0 new failures.

- [ ] **Step 5: Commit**

```bash
git add redrobin/api/lifespan_helpers.py
git commit -m "fix(daemon): broaden start_engagement exception handler (closes #137)

Pre-existing: only FileNotFoundError was caught. Malformed engagement.yaml,
PermissionError, UnicodeDecodeError all propagated and crashed uvicorn at
startup with a Python traceback. Operator running 'redrobin daemon start'
would see no banner, just a stack trace from yaml.YAMLError or similar.

Fix: catch yaml.YAMLError + OSError + UnicodeDecodeError in addition to
FileNotFoundError. Same fallback (engagement=None, wildcard scope) with a
distinct log line that names the exception type so the operator can
diagnose. FileNotFoundError keeps its own specific message ('file not
found') since the failure mode is different from 'file exists but
broken'.

Pairs with the Task 1 regression tests (test_engagement_malformed_yaml.py)
that pin this contract. Both tests turn GREEN with this change.

Spec: docs/superpowers/plans/2026-05-24-defensive-hardening.md Task 2
Backlog: closes #137."
```

---

## Task 3: WS endpoints' `bus.subscribe()` exception guard (#127)

**Files:**
- Modify: `redrobin/api/tui_ws.py`
- Modify: `redrobin/api/adapter_events_ws.py`

No TDD for this one — the failure mode (`bus.subscribe()` raising) doesn't occur in the current `EventBus` implementation. The fix is purely defensive code hygiene for future-proofing. Code review approval is the validation.

- [ ] **Step 1: Read both files**

```bash
grep -n "queue = await bus.subscribe\|finally:\|bus.unsubscribe" redrobin/api/tui_ws.py redrobin/api/adapter_events_ws.py
```

- [ ] **Step 2: Apply the guard in `tui_ws.py`**

Find the body of `tui_ws` (around lines 21-43). Currently:

```python
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = await bus.subscribe()

    try:
        snapshot_payload = await _build_initial_snapshot(websocket.app)
        await websocket.send_json(
            {"type": "working_memory_snapshot", "payload": snapshot_payload}
        )

        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("TUI WebSocket client disconnected")
    finally:
        await bus.unsubscribe(queue)
```

Modify to:

```python
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = None  # Defensive: ensure `queue` is defined before the
                  # try/finally even if bus.subscribe() raises (#127).

    try:
        queue = await bus.subscribe()
        snapshot_payload = await _build_initial_snapshot(websocket.app)
        await websocket.send_json(
            {"type": "working_memory_snapshot", "payload": snapshot_payload}
        )

        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("TUI WebSocket client disconnected")
    finally:
        if queue is not None:
            await bus.unsubscribe(queue)
```

- [ ] **Step 3: Apply the same guard in `adapter_events_ws.py`**

Find the body of `adapter_events_ws` (around lines 25-45). Same transformation: move `queue = await bus.subscribe()` INSIDE the `try` block, initialize `queue = None` before, guard the `finally` with `if queue is not None:`.

The exact lines to change:

Before:
```python
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = await bus.subscribe()

    try:
        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            if event.get("type") == "opsec.verdict":
                await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("adapter events WS disconnected")
    finally:
        await bus.unsubscribe(queue)
```

After:
```python
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = None  # Defensive: ensure `queue` is defined before the
                  # try/finally even if bus.subscribe() raises (#127).

    try:
        queue = await bus.subscribe()
        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            if event.get("type") == "opsec.verdict":
                await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("adapter events WS disconnected")
    finally:
        if queue is not None:
            await bus.unsubscribe(queue)
```

- [ ] **Step 4: Run the integration suite to check no regressions**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
REDROBIN_ALLOW_DEGRADED=1 .venv/Scripts/pytest.exe tests/integration/ -v 2>&1 | tail -10
```

Expected: same green tally as before (the WS broadcast tests at `tests/integration/test_tui_ws_broadcast.py` and `test_adapter_events_ws.py` should all still pass — happy path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add redrobin/api/tui_ws.py redrobin/api/adapter_events_ws.py
git commit -m "fix(daemon): guard WS subscribe()-then-finally against UnboundLocalError (closes #127)

Pre-existing latent bug surfaced during T6 (adapter event stream) code
review: both tui_ws.py and adapter_events_ws.py acquire the EventBus
queue OUTSIDE the try/finally:

    queue = await bus.subscribe()
    try: ...
    finally:
        await bus.unsubscribe(queue)

If bus.subscribe() raises, queue is never assigned and the finally
block hits UnboundLocalError. Today EventBus.subscribe() never raises
(it just creates an asyncio.Queue + locked-append to a list), so the
bug is purely latent — but a future bus change that introduces failure
modes (max-subscriber cap, resource limits) would tickle it.

Fix: initialize queue = None before the try, move bus.subscribe()
INSIDE the try, guard the finally with 'if queue is not None'. Happy
path unchanged. Same pattern applied symmetrically in both files.

Spec: docs/superpowers/plans/2026-05-24-defensive-hardening.md Task 3
Backlog: closes #127."
```

---

## Task 4: `RedRobinEventStream.close()` on `.cna` unload (#125)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

- [ ] **Step 1: Locate the script tail**

The `on script_unload` hook should live near the script tail, after the existing `on heartbeat_5s` block (the Layer B verdict drain hook added in #138). Verify the script tail structure:

```bash
grep -n "^on heartbeat_5s\|^on script_unload\|# ===== " adapters/cobaltstrike/redrobin.cna | tail -10
```

Expected: an `on heartbeat_5s` block exists near the tail; no `on script_unload` exists yet.

- [ ] **Step 2: Add the unload hook**

**Event-name citation (verified):** Aggressor's `on script_unload` event is documented in the Cobalt Strike Aggressor Script Manual (Fortra), section "Common Events", and used in community scripts like `harleyQu1nn/AggressorScripts/utils.cna` for the same cleanup-on-reload pattern. Fires when the Script Manager unloads or reloads a script.

Append the following block AFTER the closing `}` of the `on heartbeat_5s` hook (search for the line that just contains `}` directly closing that block — you can `grep -n "^}" adapters/cobaltstrike/redrobin.cna | tail -5` to find candidates, but the on-heartbeat closer is the last top-level `}` of the file as it stands today):

```sleep


# ===== Script unload cleanup =====
# Each .cna load creates a fresh RedRobinEventStream (Java) with a
# daemon-thread ScheduledExecutorService + persistent WebSocket. The
# instance is reachable only from $REDROBIN_EVENT_STREAM. On reload,
# Sleep clears that global, but the Java instance leaks: its thread
# keeps running, the WS stays open, and the BlockingQueue (~512KB)
# remains in memory. Over ~10-20 .cna iterations this becomes
# observable in JVM memory + thread dumps.
#
# close() (added by Task #115/Task 9 of the adapter-event-stream plan)
# does the right teardown: shutdown flag, sendClose on the WS, then
# scheduler.shutdownNow(). Safe to call multiple times (idempotent
# behind the volatile shutdown flag).
#
# The $REDROBIN_EVENT_STREAM !is $null guard handles two edge cases:
# (a) script_unload firing before init_event_stream ran (script load
# failed early), (b) operator manually nulling the global for debug.
on script_unload {
    if ($REDROBIN_EVENT_STREAM !is $null) {
        [$REDROBIN_EVENT_STREAM close];
    }
}
```

(Leading blank lines for visual separation from the heartbeat block above.)

- [ ] **Step 3: Verify the hook landed**

```bash
grep -n "on script_unload\|REDROBIN_EVENT_STREAM close" adapters/cobaltstrike/redrobin.cna
```

Expected:
- ONE `on script_unload {` line
- ONE `[$REDROBIN_EVENT_STREAM close]` line inside the hook
- (Plus the existing references to `$REDROBIN_EVENT_STREAM` in the globals, init sub, and heartbeat handler — those should be unchanged in count.)

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): release RedRobinEventStream on script_unload (closes #125)

Pre-existing leak surfaced during T11 code review: each redrobin.cna
load instantiates RedRobinEventStream (Java) with a daemon thread +
persistent WebSocket + 256-entry BlockingQueue (~512KB). On reload
(Script Manager → Unload → Load), Sleep clears \$REDROBIN_EVENT_STREAM
but the Java instance is never garbage-collected because the scheduler
thread holds a strong self-reference. After ~10-20 iterations during
development, JVM accumulates zombie threads + open WS connections.

Fix: add an 'on script_unload { ... }' hook that calls close() on the
existing instance (the close() method was implemented as part of Task 9
of the adapter-event-stream plan). Guard with !is \$null so it's safe
when the init never ran (e.g., script load failed early).

Production impact: low — most operators load the .cna once per
engagement and run. The leak only manifests during iterative .cna
development. Still a clean fix; aligns with the JVM-resource discipline
established by RedRobinHttp + RedRobinEventStream architecture.

Spec: docs/superpowers/plans/2026-05-24-defensive-hardening.md Task 4
Backlog: closes #125."
```

---

## Task 5: Smoke verification (operator-run, OPTIONAL)

**Files:** None modified. Verification only.

Plan A's three fixes are independently testable but #125 specifically needs an operator-driven smoke (Sleep + CS Client + script unload). The other two are covered by the pytest regression in Task 1 + integration suite green at Task 3.

If you (the operator) want to manually validate #125 in CS:

1. Restart daemon (`Ctrl+C`, then `redrobin daemon start ...`). Banner should still show `Engagement: tc4-smoke`.
2. In CS Client: Script Manager → Load `redrobin.cna`. Expect `[Red Robin] .cna loaded; gate active.`
3. Daemon log: `WebSocket /v1/adapters/events ... [accepted]` confirms new RedRobinEventStream connected.
4. In CS Client: Script Manager → **Unload** `redrobin.cna`.
5. Daemon log: `INFO: connection closed` should appear within ~1-2s (the close() sends a NORMAL_CLOSURE frame).
6. Reload the .cna. Banner reconnects cleanly with a NEW WS upgrade.
7. Repeat 3-5 times. Daemon log should show clean open/close pairs each cycle (no zombie connections accumulating).

If step 5's `connection closed` does NOT appear after unload, the close() call may have failed silently — report and we'll investigate.

For #137 and #127, the pytest regressions are the canonical validation; no operator-driven smoke needed unless you want to manually corrupt an engagement.yaml and see the banner warn.

---

## Self-Review (skill checklist)

**Spec coverage:** All three backlog items (#125, #127, #137) have at least one task:
- #137 → Tasks 1 (test RED) + 2 (impl GREEN)
- #127 → Task 3 (defensive guard, no test — code-review-only per the latent-bug nature)
- #125 → Task 4 (Sleep hook + optional Task 5 smoke)

**Placeholder scan:** No TBD/TODO/"implement later"/"similar to Task N". Each step contains complete code and complete commands.

**Type / name consistency:**
- `engagement_slug` (Settings field) referenced in Task 2 → matches the existing `lifespan_helpers.py:start_engagement` parameter
- `EngagementService(root=...).new(...) + .use(...)` in Task 1 fixture → matches the proven pattern from `test_engagement_active_file.py` (the prior engagement-isolation test)
- `[$REDROBIN_EVENT_STREAM close]` in Task 4 → matches the `RedRobinEventStream.close()` method signature in JAR (no args, returns void)
- `bus.unsubscribe(queue)` in Task 3 → matches existing call at `tui_ws.py:43` and `adapter_events_ws.py:45`

All references consistent. Ready for execution.
