# Data-Quality Group D — Session Re-seed + B3 Regression Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `beacon.initial` pass-through (un-deduped) in the B3 dedup gate so the `.cna` backfill re-seeds sessions after a working-memory clean — fixing the `sessions: []` regression B3 introduced (#107).

**Architecture:** B3's `derive_key` currently dedups `beacon.initial` with key `(bid, beacon.initial)`. `beacon.initial` is idempotent at the reducer (`_on_beacon_initial → upsert_session` replaces by `bid`), so deduping it gains nothing and silently drops the backfill's re-emit after a clean. The fix removes the one `beacon.initial` branch from `derive_key` (it falls through to `None` = pass-through); command events keep their composite-key dedup unchanged. One function change + doc/comment updates + test inversions + one new re-seed test.

**Tech Stack:** Python 3.12, FastAPI, asyncio, pytest + httpx TestClient, ruff/mypy/import-linter, `uv`.

---

## Pre-existing context (read once before starting)

- **Spec:** `docs/superpowers/specs/2026-05-28-data-quality-design.md` (commit `91b36a3`), §5 (Group D) is this plan; §2 (writer-split), §3 (constraints), §11 (acceptance) frame it.
- **The function being changed:** `redrobin/application/dedup_keys.py` — `derive_key(event)`. Current state (verified at HEAD `49b1aba`):

```python
def derive_key(event: dict[str, Any]) -> str | None:
    """Return the dedup key for a wire event payload, or ``None`` for
    pass-through (event types we choose not to dedup).

    Key shape: ``"<bid>|<correlation_id>|<event_type>"`` for command events,
    ``"<bid>||<event_type>"`` for ``beacon.initial`` (no correlation_id; exactly
    one initial per beacon by definition).
    """
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None

    # opsec.verdict uses `beacon_id`; the beacon.* events use `bid`.
    bid_raw = event.get("bid") or event.get("beacon_id")
    if not bid_raw:
        return None
    bid = str(bid_raw)

    if event_type == "beacon.initial":
        return f"{bid}||{event_type}"

    if event_type in _CORRELATED_TYPES:
        corr_raw = event.get("correlation_id")
        if not corr_raw:
            return None
        return f"{bid}|{corr_raw}|{event_type}"

    return None
```

- **Reducer (unchanged, for understanding):** `redrobin/application/working_memory_reducer.py` `_on_beacon_initial(payload)` builds a `Session(beacon_id=bid, …)` and calls `self._repo.upsert_session(session)`, which replaces any existing session with the same `beacon_id` (idempotent). So a re-POSTed `beacon.initial` re-seeds the session with no duplication.
- **The `.cna` backfill (unchanged, for understanding):** `adapters/cobaltstrike/redrobin.cna:437-443` runs `foreach $_rr_b (beacons()) { redrobin_emit_beacon_initial($_rr_b['id']); }` on script load — re-emitting `beacon.initial` for every active beacon. This is the re-seed mechanism B3's dedup was eating.

## Standing constraints (apply to EVERY command/step)

- All `uv` invocations use `--no-sync` (a running daemon locks `redrobin.exe`).
- Prepend `REDROBIN_ALLOW_DEGRADED=1` to the full-suite pytest (daemon-boot tests `sys.exit(2)` without an API key otherwise).
- Atomic conventional commits. **NO AI/Claude co-author trailer.**
- Production-grade, no dead code — no stale doc/comment may claim `beacon.initial` is deduped after this change.
- Branch: `wm/data-quality` (already checked out, off `dev-juan` `49b1aba`; the spec is already committed here at `91b36a3`).

## File structure

| File | Change |
|---|---|
| `redrobin/application/dedup_keys.py` | MODIFY — remove the `beacon.initial` branch in `derive_key`; update the module docstring + `derive_key` docstring so `beacon.initial` is documented as pass-through (and why). |
| `tests/unit/test_dedup_keys.py` | MODIFY — invert `test_derive_key_beacon_initial_uses_two_part_key` → assert `None`. |
| `tests/integration/test_events_router_dedup.py` | MODIFY — invert `test_beacon_initial_dedups_on_bid_and_type` → 2nd POST not deduped; ADD a re-seed test. |

This is one cohesive behavior change. The fix and the test inversions land in **one task / one commit** so the suite is never left red between commits (changing `derive_key` alone would break the two existing beacon.initial tests).

---

## Task 1: `beacon.initial` becomes pass-through (un-deduped)

**Files:**
- Modify: `redrobin/application/dedup_keys.py`
- Modify: `tests/unit/test_dedup_keys.py`
- Modify: `tests/integration/test_events_router_dedup.py`

- [ ] **Step 1: Invert the unit test**

In `tests/unit/test_dedup_keys.py`, replace the existing test (currently at lines 51-54):

```python
def test_derive_key_beacon_initial_uses_two_part_key() -> None:
    event = {"type": "beacon.initial", "bid": "B-1", "host": "DC01"}
    # No correlation_id; key is (bid, type) with empty middle slot.
    assert derive_key(event) == "B-1||beacon.initial"
```

with:

```python
def test_derive_key_beacon_initial_is_passthrough() -> None:
    # Group D: beacon.initial is NOT deduped — it is idempotent at the reducer
    # (upsert_session replaces by bid), so deduping it only breaks the .cna
    # backfill's session re-seed after a working-memory clean. Pass-through.
    event = {"type": "beacon.initial", "bid": "B-1", "host": "DC01"}
    assert derive_key(event) is None
```

- [ ] **Step 2: Invert the integration dedup test + add the re-seed test**

In `tests/integration/test_events_router_dedup.py`, replace the existing test (currently at lines 159-183):

```python
def test_beacon_initial_dedups_on_bid_and_type(daemon) -> None:
    """beacon.initial has no correlation_id; dedup key is (bid, type)."""
    _, client = daemon
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "spec_version": "0.1.0",
        "id": _VALID_ID,
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.initial",
            "bid": "B-3",
            "host": "DC01",
            "user": "CORP\\admin",
            "integrity_level": "high",
            "arch": "x64",
            "pid": 1234,
            "ppid": 5678,
        },
    }
    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)
    assert r1.status_code == 200 and r1.json().get("stored") is True
    assert r2.status_code == 200 and r2.json().get("duplicate") is True
```

with these two tests (note the added `import time` — add it to the top-of-file imports block; the file currently imports `from pathlib import Path` and `from typing import Any`, so add `import time` above `from pathlib import Path`. No `import json` needed — the re-seed test reads the live repo object, not a file):

```python
def _beacon_initial_payload(bid: str = "B-3") -> dict[str, Any]:
    return {
        "spec_version": "0.1.0",
        "id": _VALID_ID,
        "ts": "2026-05-28T00:00:00Z",
        "source": {"adapter": "cs-aggressor", "version": "1.0.0", "engagement_id": "eng-b3"},
        "event": {
            "type": "beacon.initial",
            "bid": bid,
            "host": "DC01",
            "user": "CORP\\admin",
            "integrity_level": "high",
            "arch": "x64",
            "pid": 1234,
            "ppid": 5678,
        },
    }


def test_beacon_initial_is_not_deduped(daemon) -> None:
    """Group D: beacon.initial is pass-through. A 2nd identical POST is NOT
    dropped by the dedup gate (inverts the B3 behavior) — it must re-reach the
    reducer so the .cna backfill can re-seed sessions after a clean."""
    _, client = daemon
    headers = {"Authorization": f"Bearer {_token()}"}
    payload = _beacon_initial_payload()
    r1 = client.post("/v1/events", json=payload, headers=headers)
    r2 = client.post("/v1/events", json=payload, headers=headers)
    assert r1.status_code == 200 and r1.json().get("stored") is True
    assert r2.status_code == 200 and r2.json().get("stored") is True
    assert "duplicate" not in r2.json()


def test_beacon_initial_reseeds_session(daemon) -> None:
    """End-to-end: a POSTed beacon.initial reaches the reducer and seeds the
    session; a 2nd POST (not deduped) re-seeds it idempotently. Reads the live
    working-memory repo via ``app.state.working_memory_repo`` — the reducer
    consumes the bus asynchronously, so poll until the session appears. Reading
    the repo object (not a hardcoded path) survives any future change to how the
    WM path resolves; this is the same approach the green e2e journey test uses
    (``tests/e2e/test_events_to_memory_journey.py`` reads
    ``app.state.working_memory_repo.load().sessions``)."""
    app, client = daemon
    headers = {"Authorization": f"Bearer {_token()}"}
    repo = app.state.working_memory_repo

    def _session_present(bid: str) -> bool:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                if any(s.beacon_id == bid for s in repo.load().sessions):
                    return True
            except FileNotFoundError:
                pass  # reducer hasn't written the WM file yet
            time.sleep(0.05)
        return False

    payload = _beacon_initial_payload(bid="B-7")
    client.post("/v1/events", json=payload, headers=headers)
    assert _session_present("B-7"), "reducer did not seed the session from beacon.initial"

    # 2nd POST is not deduped → reaches the reducer again → still exactly one
    # session for the bid (upsert_session replaces by bid — idempotent).
    client.post("/v1/events", json=payload, headers=headers)
    assert _session_present("B-7")
    sessions = [s for s in repo.load().sessions if s.beacon_id == "B-7"]
    assert len(sessions) == 1
```

Note for the implementer: `repo.load()` returns a `WorkingMemory` whose `.sessions` are `Session` dataclass instances (attribute access `s.beacon_id`, not dict `.get`). `repo.load()` raises `FileNotFoundError` until the reducer's first save — the poll swallows it and retries. `app.state.working_memory_repo` is the attribute the lifespan attaches in `start_working_memory`; confirm the exact name against `tests/e2e/test_events_to_memory_journey.py` if it differs.

- [ ] **Step 3: Run the inverted tests to verify they FAIL**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_dedup_keys.py::test_derive_key_beacon_initial_is_passthrough tests/integration/test_events_router_dedup.py::test_beacon_initial_is_not_deduped tests/integration/test_events_router_dedup.py::test_beacon_initial_reseeds_session -v`

Expected: all three FAIL — the unit test sees `"B-1||beacon.initial"` (not `None`); `test_beacon_initial_is_not_deduped` sees `duplicate: True` on the 2nd POST; `test_beacon_initial_reseeds_session` likely passes the first assert but the 2nd POST is still deduped (the re-seed assert may pass since the 1st POST already seeded — the FAIL signal that matters is the two `is_not_deduped` / unit assertions). The current code still dedups beacon.initial.

- [ ] **Step 4: Fix `derive_key` — remove the `beacon.initial` branch**

In `redrobin/application/dedup_keys.py`, delete these two lines (currently lines 51-52):

```python
    if event_type == "beacon.initial":
        return f"{bid}||{event_type}"
```

and replace them with this comment (so the intent is documented at the site):

```python
    # beacon.initial is intentionally NOT deduped (Group D): it is idempotent at
    # the reducer (upsert_session replaces by bid), so deduping it gains nothing
    # and would drop the .cna backfill's re-emit that re-seeds sessions after a
    # working-memory clean. It falls through to None below = pass-through.
```

After the edit, a `beacon.initial` event flows: `type` is str ✓ → `bid` present ✓ → not in `_CORRELATED_TYPES` → returns `None` (pass-through). Command events are unchanged.

- [ ] **Step 5: Update the docstrings so no stale claim remains**

In the same file, update the **module docstring** — replace the current lines (8-11):

```python
``derive_key(event)`` is a pure function that maps a wire event payload to
a stable dedup key string, or returns ``None`` for events that should
pass-through unfiltered (events without ``correlation_id`` other than
``beacon.initial``).
```

with:

```python
``derive_key(event)`` is a pure function that maps a wire event payload to
a stable dedup key string, or returns ``None`` for events we don't dedup:
events without a ``correlation_id``, AND ``beacon.initial`` — which is
idempotent at the reducer (``upsert_session`` replaces by ``bid``), so
deduping it would only break session re-seeding after a working-memory
clear (Group D).
```

And update the **`derive_key` docstring** — replace the current body (lines 34-40):

```python
    """Return the dedup key for a wire event payload, or ``None`` for
    pass-through (event types we choose not to dedup).

    Key shape: ``"<bid>|<correlation_id>|<event_type>"`` for command events,
    ``"<bid>||<event_type>"`` for ``beacon.initial`` (no correlation_id; exactly
    one initial per beacon by definition).
    """
```

with:

```python
    """Return the dedup key for a wire event payload, or ``None`` for
    pass-through (event types we choose not to dedup).

    Key shape: ``"<bid>|<correlation_id>|<event_type>"`` for command events
    (``beacon.command_input``/``beacon.command_output``/``opsec.verdict``).
    ``beacon.initial`` is pass-through (``None``) — idempotent at the reducer,
    so deduping it would break the ``.cna`` backfill's session re-seed.
    """
```

- [ ] **Step 6: Run the inverted + new tests to verify they PASS**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_dedup_keys.py tests/integration/test_events_router_dedup.py -v`

Expected: all pass — including `test_derive_key_beacon_initial_is_passthrough`, `test_beacon_initial_is_not_deduped`, `test_beacon_initial_reseeds_session`, and the unchanged command-event dedup tests (`test_derive_key_command_output_with_correlation_id`, `test_ingest_dedups_repeated_envelope`, etc., which must still dedup correctly).

- [ ] **Step 7: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```

Expected: every gate green. Pytest = 570 (post-B3 baseline) **+1** = **571** (unit: 1 inverted test, net 0; integration: 1 inverted + 1 new re-seed = net +1).

- [ ] **Step 8: Commit**

```bash
git add redrobin/application/dedup_keys.py tests/unit/test_dedup_keys.py tests/integration/test_events_router_dedup.py
git commit -m "fix(dedup): beacon.initial is pass-through, not deduped — restores session re-seed (#107)"
```

---

## Task 2: Live-beacon smoke + merge handoff (operator-run, no CI)

**Files:** none (verification + handoff).

The session re-seed is end-to-end behavior; CI proves the code paths (derive_key → None, 2nd POST not deduped, reducer seeds the session via the poll test), but the real acceptance is the live beacon.

- [ ] **Step 1: Hand the operator the smoke check**

Per spec §11 (Group D acceptance), on a clean `manual-smoke` (same setup block as the B3 runbook `docs/superpowers/runbooks/2026-05-28-b3-smoke.md` — stop daemon, wipe `working_memory.json`/`events.jsonl`/`episodic.db`/`dedup_keys.json`, restart daemon):

1. With the beacon already connected, **reload `redrobin.cna`** in CS Script Manager → the backfill (`foreach beacons()`) re-emits `beacon.initial`.
2. Open the TUI Inspector → **SESSIONS shows the active beacon** (before Group D it stayed empty because the dedup gate dropped the re-emit).
3. Verify: `python $env:USERPROFILE\.redrobin\inspect_layers.py` shows the session populated.

Expected (Group D fix): SESSIONS is populated after the reload. Pre-Group-D: SESSIONS empty despite the active beacon.

- [ ] **Step 2: On green smoke, merge to dev-juan**

Do NOT auto-merge. After the operator confirms the live smoke, the merge follows the B1.5/B2/B3 pattern: push `wm/data-quality`, open a PR → `dev-juan`, merge (CI fires on the `dev-*` push post-merge). This branch carries the data-quality spec (`91b36a3`) + Group D; groups A/C/B branch off the updated `dev-juan` afterward.

---

## Self-Review

**1. Spec coverage (§5 Group D):**
- "remove `beacon.initial` from the dedup'd path — return `None`" → Task 1 Step 4. ✓
- "Command events keep their composite-key dedup" → unchanged `_CORRELATED_TYPES` branch; covered by the still-passing command-event tests (Step 6). ✓
- "invert the unit test" → Step 1. ✓
- "invert the integration test + add a re-seed test" → Step 2. ✓
- "update doc/comments so no stale claim remains" → Steps 4 (site comment) + 5 (module + function docstrings). ✓
- "no .cna change, no reducer change" → file table lists only dedup_keys.py + 2 test files. ✓
- §11 acceptance (SESSIONS shows the beacon after clean + reload) → Task 2. ✓

**2. Placeholder scan:** No TBD/TODO. All code blocks are complete (exact before/after for every edit). The implementer note in Step 2 about the WM path is a verification instruction, not a placeholder — the path is specified (`tmp_path/wm.json`).

**3. Type consistency:** `derive_key(event: dict[str, Any]) -> str | None` unchanged. The new test helper `_beacon_initial_payload(bid: str = "B-3") -> dict[str, Any]` is defined once and reused by both new tests. `_session_present(bid: str) -> bool` is local to the re-seed test. `_token()`, `_VALID_ID`, and the `daemon` fixture are reused as-is from the existing file. No signature drift.

**4. Keep-green-between-tasks:** Task 1 lands the fix + both test inversions + the new test in ONE commit, so the suite is green after the commit (changing `derive_key` without inverting the two beacon.initial tests would leave them red — hence single task). Task 2 is operator-run, no code.
