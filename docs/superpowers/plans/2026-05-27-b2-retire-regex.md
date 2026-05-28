# B2 — Retire the legacy command-output regex extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the brittle regex extraction inside `WorkingMemoryReducer._on_beacon_command_output` (and the now-orphaned `upsert_host`/`upsert_credential`) so the LLM enricher is the *sole* working-memory extraction path — killing the `#145` kerberos false-positive and the ~85-finding flood at its source.

**Architecture:** The reducer currently does TWO jobs: (a) regex-parse `beacon.command_output` for NTLM/hostname/kerberos → write creds/hosts/findings, and (b) handle structured events (`beacon.initial`→Session, `opsec.verdict`→Finding, `working_memory.deltas`→`apply_deltas`). B2 removes (a) entirely. The enricher (`redrobin/application/enrichment/enricher.py`) already subscribes to the bus independently for `beacon.command_output`, extracts via the LLM, and publishes `working_memory.deltas` — which the reducer already merges with dedup (`apply_deltas`). So removing the regex leaves a complete, deduped extraction path. **The gate, the enricher, and all structured-event handling are untouched.**

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, ruff, mypy, import-linter, `uv`.

**Driver (from the 2026-05-27 live smoke):** a `dir /s C:\` produced ~85 IDENTICAL false `kerberos_ticket_observed` findings. Root cause = the `_KERBEROS_TICKET` regex matching the literal string `klist`/`Cached Tickets`/`krbtgt` inside FILE PATHS in the `dir` output (no real tickets), via `append_finding` (append-only, no dedup), amplified by `.cna` re-emission. The enricher's `apply_deltas` already dedups findings by `(kind, summary, source_beacon_id)`; once the regex is gone, the false finding never gets produced. Design reference: `docs/superpowers/specs/2026-05-26-working-memory-ai-enrichment-design.md` §"No dead code" + the delete-table (lines ~155-160).

**Out of scope (→ B2.1, task #108):** the tier-3 `doctor` enrichment counters. They need a cross-process design decision (doctor is a separate preflight CLI; the enricher tallies in the daemon process) and are observability, not data-quality. B2 is the retirement only.

---

## What is being removed vs kept (grep-verified 2026-05-27)

| Symbol | Location | Action | Why |
|---|---|---|---|
| `_NTLM_LINE` / `_KERBEROS_TICKET` / `_HOSTNAME_LINE` | `working_memory_reducer.py:54-69` | **DELETE** | the regex extractors |
| `_on_beacon_command_output` | `working_memory_reducer.py:153-179` | **DELETE** | the regex handler body |
| `beacon.command_output` branch in `_dispatch` | `working_memory_reducer.py:138-140` | **DELETE** | reducer stops handling it (enricher does, separately) |
| `import re` | `working_memory_reducer.py:34` | **DELETE** | orphaned after the regexes go |
| `Credential`, `Host` (reducer import) | `working_memory_reducer.py:40,42` | **DELETE from import** | only the deleted handler used them in the reducer |
| `WorkingMemoryRepo.upsert_credential` | `working_memory_repo.py:73-81` | **DELETE** | only `reducer._on_beacon_command_output:166` called it (orphaned) |
| `WorkingMemoryRepo.upsert_host` | `working_memory_repo.py:86-105` | **DELETE** | only `reducer._on_beacon_command_output:171` called it (orphaned) |
| `Finding`, `Session`, `WorkingMemoryDeltas`, `working_memory_to_snapshot` (reducer imports) | reducer | **KEEP** | used by `_on_verdict` / `_on_beacon_initial` / `_on_enrichment_deltas` |
| `append_finding` | `working_memory_repo.py:114` | **KEEP** | `_on_verdict:231` (opsec.verdict BLOCK) still uses it |
| `upsert_session` | `working_memory_repo.py:107` | **KEEP** | `_on_beacon_initial:196` uses it |
| `list_credentials` | `working_memory_repo.py:83` | **KEEP** | public read helper used by the e2e + integration tests (NOT the snapshot path — that uses `working_memory_to_snapshot`/`repo.load()`) |
| `apply_deltas` + `_merge_*` | `working_memory_repo.py:120+` | **KEEP** | the enricher's single-writer merge |

> **Import cleanup is ruff-driven:** after deleting code, `uv run --no-sync ruff check .` flags any now-unused import (e.g. `re`, `Credential`, `Host`). Remove exactly what ruff flags — do not guess. In the *repo* module, `Credential`/`Host` are likely still referenced by `apply_deltas`/`_merge_host`; trust ruff, not this note.

## Tests affected (grep-verified)

- `tests/integration/test_working_memory_reducer.py`: 3 pure-regex tests **deleted**; 2 regex-*dependent* tests (`test_reducer_republishes_snapshot_after_mutation`, `test_lifespan_starts_reducer_task`) **rewritten** to the new path; 1 new "command_output is ignored" test **added**. The structured-event tests (beacon.initial, opsec verdicts, unknown, `test_working_memory_deltas_event_merges_and_reemits`) are untouched.
- `tests/e2e/test_memory_journey.py`: `test_beacon_output_propagates_to_snapshot` **rewritten** (it replays a regex command_output expecting creds/host; that path is gone + the enricher is off in CI).
- `tests/e2e/test_events_to_memory_journey.py`: `test_post_events_canonical_command_output_reaches_reducer` **deleted** (audit catch — the canonical-envelope twin asserting cred `bob`/host `WIN11` from the regex; the canonical→reducer path stays covered by the sibling `test_post_events_canonical_beacon_initial_creates_session`).
- `tests/unit/tui_textual/test_inspector.py`: uses `kerberos_ticket_observed` only as **fixture data** to test rendering — **untouched** (it constructs the finding directly, never the regex).

---

## Task 1: Decouple the two regex-dependent reducer integration tests

**Files:**
- Modify: `tests/integration/test_working_memory_reducer.py` (`test_reducer_republishes_snapshot_after_mutation` ~226-262, `test_lifespan_starts_reducer_task` ~265-291)

These two tests use a regex `beacon.command_output` purely as a *vehicle* to trigger a mutation; their real assertions are "snapshot re-emitted" and "lifespan starts the reducer". Rewrite them onto surviving event types so they pass *before and after* the regex removal.

- [ ] **Step 1: Rewrite `test_reducer_republishes_snapshot_after_mutation` to use `beacon.initial`**

Replace the whole test body with:

```python
@pytest.mark.asyncio
async def test_reducer_republishes_snapshot_after_mutation(tmp_path):
    """#146: after handling a mutating event the reducer must publish a
    complete working_memory_snapshot back to the bus so the TUI updates live.
    Uses beacon.initial (a structured event) — the regex command_output path
    was retired in B2."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-reemit")
    # A second subscriber stands in for a connected TUI client.
    observer = await bus.subscribe()
    _, task = await _start_reducer(bus, repo, "eng-reemit")
    try:
        await bus.publish(
            {
                "type": "beacon.initial",
                "payload": {
                    "bid": "B-9",
                    "host": "DC01",
                    "user": "CORP\\admin",
                    "integrity_level": "high",
                },
            }
        )
        snapshot = None
        for _ in range(50):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except Exception:
                continue
            if evt.get("type") == "working_memory_snapshot":
                snapshot = evt
                break
        assert snapshot is not None, "reducer did not re-publish a snapshot"
        sessions = snapshot["payload"]["sessions"]
        assert any(s["beacon_id"] == "B-9" for s in sessions)
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Rewrite `test_lifespan_starts_reducer_task` to drive a `working_memory.deltas` event**

Replace the whole test body with (note: keep `REDROBIN_ALLOW_DEGRADED=1` so the keyless boot does not `sys.exit(2)`):

```python
@pytest.mark.asyncio
async def test_lifespan_starts_reducer_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-test-001")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.reducer_task is not None
        assert not app.state.reducer_task.done()
        # Drive the live extraction path (enricher -> deltas -> reducer). The
        # enricher is off without an API key, so publish the deltas directly.
        await app.state.event_bus.publish(
            {
                "type": "working_memory.deltas",
                "payload": {
                    "source_beacon_id": "B-100",
                    "credentials": [
                        {
                            "domain": "CORP",
                            "username": "alice",
                            "secret_type": "plaintext",
                            "secret": "pw",
                        }
                    ],
                },
            }
        )
        await asyncio.sleep(0.3)
        creds = app.state.working_memory_repo.list_credentials()
        assert any(getattr(c, "username", None) == "alice" for c in creds)
```

- [ ] **Step 3: Run the two rewritten tests — they must PASS (regex still present, but no longer used by them)**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_working_memory_reducer.py::test_reducer_republishes_snapshot_after_mutation tests/integration/test_working_memory_reducer.py::test_lifespan_starts_reducer_task -v`
Expected: PASS (2 passed).

> If `sessions` has no `beacon_id` key or `working_memory.deltas` credential merge differs, the implementer must inspect the real snapshot/`apply_deltas` shapes (`working_memory_to_snapshot`, `_merge` in `working_memory_repo.py`) and adjust the assertion — do not change production code in this task.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_working_memory_reducer.py
git commit -m "test(reducer): decouple snapshot/lifespan tests from the regex path"
```

---

## Task 2: Decouple the e2e memory-journey test from the regex

**Files:**
- Modify: `tests/e2e/test_memory_journey.py` (`test_beacon_output_propagates_to_snapshot` ~56-103)
- Modify: `tests/e2e/test_events_to_memory_journey.py` (DELETE `test_post_events_canonical_command_output_reaches_reducer` ~71-119 — audit catch)

The e2e replays a `beacon.command_output` carrying NTLM+hostname and asserts the snapshot exposes the cred + host — pure regex behavior. After B2 the regex is gone and the enricher is disabled in CI (no API key), so replay a structured `beacon.initial` and assert the **session** propagates end-to-end (wire → replay endpoint → bus → reducer → snapshot).

- [ ] **Step 1: (audit-confirmed) `/v1/events/replay` accepts `beacon.initial` as a bus-shape dict**

The audit verified `redrobin/api/events_router.py::replay_event` **skips schema validation** ("the operator owns the trace contents") and publishes a non-envelope dict verbatim onto the bus. So `{"type":"beacon.initial","payload":{"bid","host","user","integrity_level"}}` is accepted (202) and the reducer's `_on_beacon_initial` reads exactly those fields. No schema check needed — the existing `tests/e2e/test_memory_journey.py` already replays a bus-shape dict the same way. **Do NOT** route this through `POST /v1/events` — that path IS schema-validated (`additionalProperties:false`, requires `arch`/`pid`/`ppid`) and would 400.

- [ ] **Step 2: Rewrite the test (rename to reflect the structured path)**

Replace `test_beacon_output_propagates_to_snapshot` (the whole function) with:

```python
def test_beacon_initial_propagates_to_snapshot(daemon):
    app, client = daemon
    token = _token()

    payload = {
        "type": "beacon.initial",
        "payload": {
            "bid": "B-1",
            "host": "WIN10",
            "user": "CORP\\alice",
            "integrity_level": "high",
        },
    }
    resp = client.post(
        "/v1/events/replay",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202

    # Poll the live working-memory repo until the reducer records the session.
    deadline = time.time() + 2.0
    repo = app.state.working_memory_repo
    while time.time() < deadline:
        sessions = repo.load().sessions
        if any(s.beacon_id == "B-1" for s in sessions):
            break
        time.sleep(0.05)
    else:
        pytest.fail("reducer did not record session within 2s; sessions=%r" % repo.load().sessions)

    # Connect a fresh WS — the initial snapshot is built from the live repo.
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "working_memory_snapshot"
    sessions = snap["payload"]["sessions"]
    assert any(s["beacon_id"] == "B-1" and s["hostname"] == "WIN10" for s in sessions), sessions
```

Also update the module docstring (lines ~1-18) to describe the `beacon.initial → Session → snapshot` path instead of the regex creds/host path.

- [ ] **Step 3: Check for any OTHER regex-dependent e2e tests in the file**

Run: `grep -n "beacon.command_output\|NTLM\|Hostname:\|list_credentials\|\"hosts\"" tests/e2e/test_memory_journey.py`
Expected: the audit found ONE the first pass missed — `tests/e2e/test_events_to_memory_journey.py::test_post_events_canonical_command_output_reaches_reducer` (~71-119) POSTs a canonical `beacon.command_output` envelope (NTLM+hostname) via `/v1/events` and asserts cred `bob`/host `WIN11` from the regex. **DELETE that whole test function** — the canonical→reducer contract stays covered by its sibling `test_post_events_canonical_beacon_initial_creates_session` (uses `beacon.initial`), and reducer-ignores-command_output is covered by the new Task 3 test. Then grep `beacon.command_output` across `tests/e2e/` + `tests/integration/test_working_memory_reducer.py` and confirm no other test asserts a cred/host/`kerberos_ticket_observed` from the reducer (only the Task-3 ignored-test + the enricher tests should remain).

- [ ] **Step 4: Run the e2e — must PASS**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/e2e/test_memory_journey.py tests/e2e/test_events_to_memory_journey.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_memory_journey.py tests/e2e/test_events_to_memory_journey.py
git commit -m "test(e2e): drive memory-journey via beacon.initial; drop the regex command_output twin"
```

---

## Task 3: Retire the reducer regex extraction (TDD)

**Files:**
- Modify: `redrobin/application/working_memory_reducer.py` (docstring 7-14, imports 33-48, regexes 52-69, `_dispatch` 138-140, `_on_beacon_command_output` 153-179)
- Modify: `tests/integration/test_working_memory_reducer.py` (delete 3 regex tests, add 1 ignored-test)

- [ ] **Step 1: Add the failing "command_output is ignored" test**

Add this test to `tests/integration/test_working_memory_reducer.py` (after the structured-event tests):

```python
@pytest.mark.asyncio
async def test_beacon_command_output_is_ignored_by_reducer(tmp_path):
    """B2: the reducer no longer regex-extracts from command output — the LLM
    enricher is the sole extraction path (it publishes working_memory.deltas,
    a separate bus consumer). A command_output carrying NTLM / hostname /
    kerberos patterns must therefore produce NO creds / hosts / findings via
    the reducer."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-noextract")
    _, task = await _start_reducer(bus, repo, "eng-noextract")
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-001",
                    "output": (
                        "Username: CORP\\admin\n"
                        "NTLM: aad3b435b51404eeaad3b435b51404ee:"
                        "31d6cfe0d16ae931b73c59d7e0c089c0\n"
                        "Hostname: dc01\n"
                        "klist\nCached Tickets: (1)\n"
                    ),
                },
            }
        )
        await asyncio.sleep(0.2)
        wm = repo.load()
        assert wm.credentials == []
        assert wm.hosts == []
        assert wm.findings == []
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run it — must FAIL (the regex still extracts today)**

Run: `uv run --no-sync pytest tests/integration/test_working_memory_reducer.py::test_beacon_command_output_is_ignored_by_reducer -v`
Expected: FAIL (assertions fail — the regex produced a credential / host / kerberos finding).

- [ ] **Step 3: Delete the 3 pure-regex tests**

In `tests/integration/test_working_memory_reducer.py`, delete these three test functions entirely:
- `test_beacon_command_output_with_ntlm_writes_credential`
- `test_beacon_command_output_hostname_writes_host`
- `test_beacon_command_output_kerberos_appends_finding`

- [ ] **Step 4: Delete the regex path in the reducer**

In `redrobin/application/working_memory_reducer.py`:

1. Delete the regex definitions and their comments (the `_NTLM_LINE`, `_KERBEROS_TICKET`, `_HOSTNAME_LINE` blocks, ~lines 52-69).
2. Delete the `beacon.command_output` branch in `_dispatch`:

```python
        if etype == "beacon.command_output":
            self._on_beacon_command_output(payload)
            return True
```

3. Delete the entire `_on_beacon_command_output` method (def + body).
4. Fix the imports — `import re` goes; trim the `domain.memory` import to what remains:

```python
from redrobin.domain.memory import (
    Finding,
    Session,
    WorkingMemoryDeltas,
    working_memory_to_snapshot,
)
```

5. Update the module docstring dispatch table (lines ~7-14): remove the `beacon.command_output → regex-parse …` bullet so it reads:

```
* ``beacon.initial``         → upsert a ``Session`` keyed by ``bid``.
* ``opsec.verdict``          → when ``verdict == "BLOCK"``, append a finding.
* ``working_memory.deltas``  → merge an enrichment deltas batch via
  ``WorkingMemoryRepo.apply_deltas`` (single writer; provenance system-supplied).

``beacon.command_output`` is consumed by the LLM enricher
(``redrobin/application/enrichment/enricher.py``), a separate bus consumer that
publishes ``working_memory.deltas`` — the reducer no longer parses it.
```

- [ ] **Step 5: Run the new test (PASS) + the reducer suite**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/integration/test_working_memory_reducer.py -v`
Expected: PASS — the ignored-test passes, the 3 regex tests are gone, all structured-event tests still pass.

- [ ] **Step 6: Lint to catch any leftover unused import**

Run: `uv run --no-sync ruff check redrobin/application/working_memory_reducer.py`
Expected: `All checks passed!` — if ruff flags an unused import (e.g. a missed `re`/`Credential`/`Host`), remove exactly that, then re-run.

- [ ] **Step 7: Commit**

```bash
git add redrobin/application/working_memory_reducer.py tests/integration/test_working_memory_reducer.py
git commit -m "refactor(reducer): retire the command-output regex extraction (kills #145 kerberos FP)"
```

---

## Task 4: Retire the orphaned repo helpers `upsert_host` / `upsert_credential`

**Files:**
- Modify: `redrobin/infrastructure/persistence/working_memory_repo.py` (`upsert_credential` 73-81, `upsert_host` 86-105)

- [ ] **Step 1: Prove they are orphaned (no callers outside the now-deleted regex path)**

Run: `grep -rn "upsert_host\|upsert_credential" redrobin/ tests/`
Expected: ZERO hits in `redrobin/` (the reducer call sites are gone after Task 3). If any non-test caller remains, STOP and report — they are not orphaned. Test hits, if any, get deleted in Step 3.

- [ ] **Step 2: Delete the two methods**

In `redrobin/infrastructure/persistence/working_memory_repo.py`, delete the entire `upsert_credential` method (73-81) and the entire `upsert_host` method (86-105). Leave `list_credentials`, `upsert_session`, `append_finding`, and `apply_deltas` intact.

- [ ] **Step 3: Delete any unit test that exercises only those methods**

Run: `grep -rn "upsert_host\|upsert_credential" tests/`
For each hit in a test that *directly* calls `repo.upsert_host(...)` / `repo.upsert_credential(...)`, delete that test (the behavior is covered by `apply_deltas` tests now). Update comments that merely *mention* the methods (e.g. `tests/e2e/test_memory_journey.py` line ~101 was already replaced in Task 2).

- [ ] **Step 4: Lint — remove any now-unused import**

Run: `uv run --no-sync ruff check redrobin/infrastructure/persistence/working_memory_repo.py`
Expected: `All checks passed!`. If ruff flags `Credential` or `Host` as unused (i.e. `apply_deltas`/`_merge_*` reference them only via the deltas objects, not by name), remove exactly those from the import block and re-run. If ruff is silent, leave the imports — they are still used.

- [ ] **Step 5: Run the repo + reducer + e2e suites**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/test_working_memory_repo.py tests/integration/test_working_memory_reducer.py tests/e2e/test_memory_journey.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add redrobin/infrastructure/persistence/working_memory_repo.py tests/
git commit -m "refactor(repo): delete orphaned upsert_host/upsert_credential (regex path retired)"
```

---

## Task 5: Full CI gate + final whole-branch review

**Files:** none (verification only)

- [ ] **Step 1: Run every real CI gate**

Run:
```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: all green. Pytest count = previous total minus the 3 deleted regex tests plus the 1 ignored-test (net −2), minus any deleted upsert unit tests.

- [ ] **Step 2: Grep-prove no dead references remain**

Run: `grep -rn "_NTLM_LINE\|_KERBEROS_TICKET\|_HOSTNAME_LINE\|_on_beacon_command_output\|upsert_host\|upsert_credential" redrobin/`
Expected: ZERO hits (all retired; no stubs, no commented-out code).

- [ ] **Step 3: Confirm the enricher path is intact**

Run: `grep -n "beacon.command_output\|working_memory.deltas" redrobin/application/enrichment/enricher.py redrobin/application/working_memory_reducer.py`
Expected: the enricher still consumes `beacon.command_output` (its own loop) and publishes `working_memory.deltas`; the reducer still handles `working_memory.deltas` via `_on_enrichment_deltas`. The extraction path is complete without the regex.

- [ ] **Step 4: Dispatch the final whole-branch code review** (subagent-driven-development handles this) and address any findings.

---

## Self-Review (run before handing off)

**1. Spec coverage:** spec §"No dead code" + delete-table → Tasks 3 & 4 delete exactly the regexes + `_on_beacon_command_output` + `upsert_host`/`upsert_credential`; §"keep" (append_finding/upsert_session) honored. Doctor counters explicitly deferred to B2.1. ✓

**2. Placeholder scan:** no "TBD"/"handle edge cases"/"similar to" — every step shows the code or the exact grep/command. Import cleanup is delegated to ruff *with an explicit rule* (remove what it flags), which is deterministic, not a placeholder. ✓

**3. Type/name consistency:** test helper names (`_new_repo`, `_start_reducer`, `_drain`) match the existing file; event types (`beacon.initial`, `working_memory.deltas`, `opsec.verdict`) match the wire/bus vocabulary; kept methods (`list_credentials`, `upsert_session`, `append_finding`, `apply_deltas`) match `working_memory_repo.py`. ✓

**4. Green-between-tasks:** Tasks 1-2 rewrite regex-dependent tests onto surviving paths *before* Task 3 removes the regex, so the suite is green after every task. ✓
