# Plan I: TUI Live-State (#146 / #139 / #140 / #143) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the TUI show live, complete engagement state — today the Working-Memory panel + Memory Inspector are frozen at connect-time, the snapshot omits 9 of 14 fields, the Inspector has no findings/credentials sections, and the Sessions panel is always empty.

**Architecture:** One root cause drives #146/#139/#140: the daemon emits a `working_memory_snapshot` ONCE at connect (`tui_ws.py`) with only 5 fields, and the reducer never re-publishes after mutating working memory. Fix = a single complete serializer `working_memory_to_snapshot()` (in `domain/`, the inverse of the existing `tui/snapshot_adapter.working_memory_from_snapshot`) used by BOTH the connect-time snapshot (closes #139) AND a new reducer re-emit to the bus after every handled event (closes #146); `tui_ws` already forwards arbitrary bus events and `ws_client` already routes `working_memory_snapshot`, so the front end is unchanged. #140 adds CREDENTIALS+FINDINGS sections to the inspector (data already in the snapshot). #143 is separate: fix the Sessions panel field names (it reads `id/name/host` but `Session` serializes `beacon_id/hostname/user/integrity`) and make the `.cna` emit `beacon.initial` (the only live-beacon-smoke-gated piece).

**Tech Stack:** Python 3.13 (FastAPI daemon, asyncio EventBus, Textual/prompt_toolkit TUI), pytest/pytest-asyncio, Cobalt Strike Aggressor/Sleep (`.cna`, Task 5 only).

**Verification model:** Tasks 1-4 + the daemon side of 5 are unit/integration-tested (no beacon). The `.cna` `beacon.initial` emit (Task 5) has NO offline Sleep linter → validated ONLY by the live-beacon operator-smoke in Task 6 (the project's standing rule: operator-smoke is the only real `.cna` acceptance). Run the suite with `REDROBIN_ALLOW_DEGRADED=1 uv run pytest tests/ ...` (the bare/whole-tree collection has a pre-existing `tests.e2e` quirk + API-key boot-validation that the flag + explicit paths avoid).

**Layer note (verified):** `working_memory_from_snapshot` lives in `tui/` and depends only on `domain` (per its docstring + the `no-presentation-from-application` import-linter contract). Its inverse must be importable by the reducer (`application`) and `tui_ws` (`api`), so `working_memory_to_snapshot` goes in **`domain/memory.py`** (lowest layer; importable by all). It is `asdict(wm)` — `repo.save()` already serializes via `asdict` and the adapter's `.get()` keys match the dataclass field names exactly.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/domain/memory.py` | memory dataclasses | ADD `working_memory_to_snapshot(wm) -> dict` (== `asdict(wm)`); import `asdict` |
| `redrobin/api/tui_ws.py` | TUI WS endpoint | `_build_initial_snapshot` uses the new serializer (all 14 fields, #139); drop the now-dead `_serialize` |
| `redrobin/application/working_memory_reducer.py` | bus→WM reducer | `_dispatch` returns bool; `run()` re-publishes `working_memory_snapshot` after each handled event (#146) |
| `redrobin/tui/inspector.py` | inspector overlay text | ADD CREDENTIALS + FINDINGS sections (#140) |
| `redrobin/tui/app.py` | TUI panels | fix `_render_sessions` field names (#143-c1) |
| `adapters/cobaltstrike/redrobin.cna` | CS adapter | ADD `redrobin_emit_beacon_initial` + `on beacon_initial` + `beacons()` backfill + manifest `events_emitted` (#143-c2) |
| `tests/unit/tui/test_snapshot_adapter.py` | serializer round-trip | APPEND #139 round-trip test |
| `tests/integration/test_tui_ws_broadcast.py` | connect snapshot | APPEND #139 full-field connect-time test |
| `tests/integration/test_working_memory_reducer.py` | reducer re-emit | APPEND #146 re-emit test |
| `tests/unit/test_inspector.py` | inspector render | APPEND #140 findings/creds test |
| `tests/unit/test_tui_panels.py` | sessions panel | APPEND #143-c1 sessions render test |

`#147` (Inspector "LESSONS" = latest verdict) is DEFERRED — it needs a new lessons-streaming path (longterm/episodic repos → TUI), not the WM snapshot pipeline. Stays in Plan J (root cause recorded in `backlog.md` #147).

---

### Task 1: `working_memory_to_snapshot` serializer + complete connect-time snapshot (#139)

**Files:**
- Modify: `redrobin/domain/memory.py` (add serializer + `asdict` import)
- Modify: `redrobin/api/tui_ws.py:49-82` (`_build_initial_snapshot` + remove `_serialize`)
- Test: `tests/unit/tui/test_snapshot_adapter.py` (append), `tests/integration/test_tui_ws_broadcast.py` (append)

- [ ] **Step 1: Write the failing round-trip test**

Append to `tests/unit/tui/test_snapshot_adapter.py`:

```python
def test_to_snapshot_round_trips_all_fields():
    """#139: working_memory_to_snapshot must be the complete inverse of
    working_memory_from_snapshot — every WorkingMemory field survives the
    round-trip (the connect-time snapshot used to drop 9 of 14)."""
    from redrobin.domain.memory import (
        Account, Credential, Finding, Host, Objective, Session, ToolUsage,
        WorkingMemory, working_memory_to_snapshot,
    )
    from redrobin.tui.snapshot_adapter import working_memory_from_snapshot

    wm = WorkingMemory(
        engagement_id="acme-q2",
        client="Acme Corp",
        scope_in_scope=["10.0.0.0/24"],
        scope_out_of_scope=["10.0.0.1"],
        objectives=[Objective(id="o1", goal="DC", progress_pct=40, milestones=["foothold"])],
        current_phase="lateral",
        kill_chain_status={"recon": "complete"},
        rules_of_engagement=["no DoS"],
        hosts=[Host(name="DC01", os="Win2022")],
        accounts=[Account(name="corp\\svc", kind="ntlm")],
        defenses_observed=["CrowdStrike"],
        tools_used=[ToolUsage(name="nanodump", used_on="DC01", used_at="2026-05-10")],
        credentials=[Credential(domain="CORP", username="admin", ntlm_hash="a" * 32)],
        findings=[Finding(kind="opsec_block", summary="blocked mimikatz")],
        sessions=[Session(beacon_id="B-1", hostname="DC01", user="corp\\admin", integrity="High")],
    )

    snap = working_memory_to_snapshot(wm)
    assert isinstance(snap, dict)
    rebuilt = working_memory_from_snapshot(snap)
    assert rebuilt == wm
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/tui/test_snapshot_adapter.py::test_to_snapshot_round_trips_all_fields -v`
Expected: FAIL — `ImportError: cannot import name 'working_memory_to_snapshot' from 'redrobin.domain.memory'`.

- [ ] **Step 3: Add the serializer to `domain/memory.py`**

In `redrobin/domain/memory.py`, change the import line `from dataclasses import dataclass, field` to:

```python
from dataclasses import asdict, dataclass, field
```

At the END of the file (after `ContextSummary`), add:

```python
def working_memory_to_snapshot(wm: WorkingMemory) -> dict:
    """Serialize a :class:`WorkingMemory` to the WS ``working_memory_snapshot``
    payload dict — the inverse of
    ``redrobin.tui.snapshot_adapter.working_memory_from_snapshot``.

    Single source of truth for BOTH the connect-time snapshot
    (``tui_ws._build_initial_snapshot``) and the reducer's live re-emit
    (#146). ``asdict`` emits every field with dataclass field names as keys
    (nested dataclasses → nested dicts), which is exactly what the adapter's
    ``.get()`` calls read; ``repo.save()`` already persists via ``asdict``.
    Lives in ``domain`` so the reducer (application) and tui_ws (api) can both
    import it without crossing the presentation-layer boundary.
    """
    return asdict(wm)
```

- [ ] **Step 4: Run the round-trip test to verify it passes**

Run: `uv run pytest tests/unit/tui/test_snapshot_adapter.py::test_to_snapshot_round_trips_all_fields -v`
Expected: PASS.

- [ ] **Step 5: Rewrite `_build_initial_snapshot` to use the serializer + drop `_serialize`**

In `redrobin/api/tui_ws.py`, replace `_build_initial_snapshot` AND the `_serialize` helper (lines 49-82) with:

```python
async def _build_initial_snapshot(app) -> dict:
    """Build a complete working_memory_snapshot payload from the live repo.

    Uses ``working_memory_to_snapshot`` so the connect-time frame carries ALL
    14 WorkingMemory fields (#139). Tolerates missing repo / missing file by
    serializing an empty WorkingMemory, which matters because some tests
    instantiate ``create_app()`` without a working-memory repo.
    """
    repo = getattr(app.state, "working_memory_repo", None)
    engagement_id = getattr(app.state, "engagement_id", None)
    if repo is None or not repo.exists():
        return working_memory_to_snapshot(WorkingMemory(engagement_id=engagement_id or ""))
    return working_memory_to_snapshot(repo.load())
```

Add the imports near the top of `tui_ws.py` (after the existing `from redrobin.application.transport_auth import get_or_create_token` line):

```python
from redrobin.domain.memory import WorkingMemory, working_memory_to_snapshot
```

- [ ] **Step 6: Write the failing connect-time field test**

Append to `tests/integration/test_tui_ws_broadcast.py`:

```python
def test_ws_initial_snapshot_carries_all_fields(app_client):
    """#139: the connect-time snapshot must include the fields that used to be
    dropped (scope/objectives/kill_chain/accounts/etc.), not just the 5
    collections."""
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        payload = first["payload"]
        for key in (
            "client", "scope_in_scope", "scope_out_of_scope", "objectives",
            "current_phase", "kill_chain_status", "rules_of_engagement",
            "accounts", "defenses_observed", "tools_used",
            "hosts", "credentials", "findings", "sessions",
        ):
            assert key in payload, f"snapshot missing {key}"
```

- [ ] **Step 7: Run the connect-time test + the full TUI-WS + snapshot suites**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run pytest tests/integration/test_tui_ws_broadcast.py tests/unit/tui/test_snapshot_adapter.py -v`
Expected: all PASS (the existing `test_ws_sends_initial_snapshot` still passes — payload still has `type`+`payload`).

- [ ] **Step 8: Commit**

```bash
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" add redrobin/domain/memory.py redrobin/api/tui_ws.py tests/unit/tui/test_snapshot_adapter.py tests/integration/test_tui_ws_broadcast.py
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" commit -m "feat(tui): complete working_memory_snapshot serializer + connect-time fields (#139)"
```

---

### Task 2: Reducer re-emits the snapshot after each event (#146)

**Files:**
- Modify: `redrobin/application/working_memory_reducer.py:85-117` (`run()` + `_dispatch` + new `_publish_snapshot`)
- Test: `tests/integration/test_working_memory_reducer.py` (append)

- [ ] **Step 1: Write the failing re-emit test**

Append to `tests/integration/test_working_memory_reducer.py`:

```python
@pytest.mark.asyncio
async def test_reducer_republishes_snapshot_after_mutation(tmp_path):
    """#146: after handling a mutating event the reducer must publish a
    complete working_memory_snapshot back to the bus so the TUI updates live."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-reemit")
    # A second subscriber stands in for a connected TUI client.
    observer = await bus.subscribe()
    _, task = await _start_reducer(bus, repo, "eng-reemit")
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-9",
                    "output": "Username: CORP\\admin\nNTLM: " + ("a" * 32) + ":" + ("b" * 32) + "\n",
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
        creds = snapshot["payload"]["credentials"]
        assert any(c["username"] == "admin" and c["domain"] == "CORP" for c in creds)
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_working_memory_reducer.py::test_reducer_republishes_snapshot_after_mutation -v`
Expected: FAIL — no `working_memory_snapshot` event appears (assert `snapshot is not None` fails).

- [ ] **Step 3: Make `_dispatch` report whether it handled a mutating event**

In `redrobin/application/working_memory_reducer.py`, replace `_dispatch` (lines 107-117) with:

```python
    def _dispatch(self, evt: dict[str, Any]) -> bool:
        """Dispatch one event to its handler. Returns True if the event was a
        handled (working-memory-mutating) type, so ``run()`` knows to re-emit
        the snapshot."""
        etype = evt.get("type")
        payload_raw = evt.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        if etype == "beacon.command_output":
            self._on_beacon_command_output(payload)
            return True
        elif etype == "beacon.initial":
            self._on_beacon_initial(payload)
            return True
        elif etype == "opsec.verdict":
            self._on_verdict(payload)
            return True
        # else: silently ignore — the bus is fan-out, not all events are for us.
        return False
```

- [ ] **Step 4: Re-emit the snapshot from `run()` + add `_publish_snapshot`**

In the same file, replace the `run()` loop body (lines 93-105) with:

```python
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                try:
                    mutated = self._dispatch(evt)
                except Exception:
                    logger.exception(
                        "reducer failed to process event type=%s", evt.get("type")
                    )
                    mutated = False
                if mutated:
                    await self._publish_snapshot()
        finally:
            await self._bus.unsubscribe(sub)
```

Then add this method immediately after `run()` (before `_dispatch`):

```python
    async def _publish_snapshot(self) -> None:
        """Publish a COMPLETE working_memory_snapshot to the bus so connected
        TUI clients update live (#146). One re-emit per handled event (not per
        repo.save), full snapshot (not a delta) — payloads are tiny. Safe from
        loops: the reducer also receives this event, but ``working_memory_snapshot``
        is not in ``_dispatch``'s table so it is ignored (returns False)."""
        try:
            wm = self._repo.load()
        except FileNotFoundError:
            return
        await self._bus.publish(
            {"type": "working_memory_snapshot", "payload": working_memory_to_snapshot(wm)}
        )
```

Add the import (with the existing domain import at line 37):

```python
from redrobin.domain.memory import (
    Credential,
    Finding,
    Host,
    Session,
    working_memory_to_snapshot,
)
```

- [ ] **Step 5: Run the re-emit test + the full reducer suite**

Run: `uv run pytest tests/integration/test_working_memory_reducer.py -v`
Expected: all PASS (existing reducer tests unaffected — they assert repo state, which still mutates; the extra bus event is ignored by their assertions).

- [ ] **Step 6: Commit**

```bash
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" add redrobin/application/working_memory_reducer.py tests/integration/test_working_memory_reducer.py
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" commit -m "feat(tui): reducer re-publishes working_memory_snapshot after each event (#146)"
```

---

### Task 3: Inspector CREDENTIALS + FINDINGS sections (#140)

**Files:**
- Modify: `redrobin/tui/inspector.py:90-97` (insert sections after TOOLS, before LESSONS)
- Test: `tests/unit/test_inspector.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_inspector.py`:

```python
def test_inspector_renders_credentials_and_findings():
    from redrobin.domain.memory import Credential, Finding
    wm = WorkingMemory(
        engagement_id="x",
        credentials=[Credential(domain="CORP", username="admin", ntlm_hash="a" * 32, source_beacon_id="B-1")],
        findings=[Finding(kind="opsec_block", summary="blocked mimikatz", source_beacon_id="B-1")],
    )
    out = render_inspector(wm)
    assert "CREDENTIALS (1)" in out
    assert "CORP\\admin" in out
    assert "FINDINGS (1)" in out
    assert "opsec_block" in out
    assert "blocked mimikatz" in out


def test_inspector_empty_credentials_and_findings():
    wm = WorkingMemory(engagement_id="x")
    out = render_inspector(wm)
    assert "CREDENTIALS (0)" in out
    assert "(none captured)" in out
    assert "FINDINGS (0)" in out
    assert "(none yet)" in out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_inspector.py::test_inspector_renders_credentials_and_findings -v`
Expected: FAIL — `assert "CREDENTIALS (1)" in out` fails (no such section).

- [ ] **Step 3: Add the sections to `inspector.py`**

In `redrobin/tui/inspector.py`, find the TOOLS block that ends at line 97 (the `sections.append("")` right after the `if not wm.tools_used:` block) and the LESSONS block that starts at line 99 (`sections.append(f"{labels.tui.lessons} ...")`). Insert BETWEEN them (after the TOOLS trailing `sections.append("")`, before the LESSONS header):

```python
    sections.append(f"CREDENTIALS ({len(wm.credentials)})")
    for cred in wm.credentials:
        sections.append(
            f"  {cred.domain}\\{cred.username} "
            f"hash={cred.ntlm_hash or '-'} src={cred.source_beacon_id or '-'}"
        )
    if not wm.credentials:
        sections.append("  (none captured)")
    sections.append("")

    sections.append(f"FINDINGS ({len(wm.findings)})")
    for finding in wm.findings:
        sections.append(
            f"  [{finding.kind}] {finding.summary} (src={finding.source_beacon_id or '-'})"
        )
    if not wm.findings:
        sections.append("  (none yet)")
    sections.append("")
```

(Hardcoded titles like the existing SCOPE/ROE/KILL CHAIN sections — no i18n label needed.)

- [ ] **Step 4: Run the test + the full inspector suite**

Run: `uv run pytest tests/unit/test_inspector.py -v`
Expected: all PASS (existing inspector tests unaffected — new sections are additive).

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" add redrobin/tui/inspector.py tests/unit/test_inspector.py
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" commit -m "feat(tui): inspector shows CREDENTIALS + FINDINGS sections (#140)"
```

---

### Task 4: Fix the Sessions panel field names (#143-c1)

**Files:**
- Modify: `redrobin/tui/app.py:92-104` (`_render_sessions`)
- Test: `tests/unit/test_tui_panels.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_tui_panels.py`:

```python
def test_render_sessions_uses_session_field_names():
    """#143: a serialized Session has keys beacon_id/hostname/user/integrity,
    NOT id/name/host — the panel must read the real keys or it shows '?'."""
    from redrobin.tui.app import _render_sessions
    from redrobin.tui.state import TUIState

    state = TUIState()
    state.snapshot["sessions"] = [
        {"beacon_id": "B-42", "hostname": "DC01", "user": "CORP\\admin", "integrity": "High"}
    ]
    out = _render_sessions(state)
    assert "B-42" in out
    assert "DC01" in out
    assert "?" not in out


def test_render_sessions_empty():
    from redrobin.tui.app import _render_sessions
    from redrobin.tui.state import TUIState

    assert _render_sessions(TUIState()) == "(no active sessions)"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_tui_panels.py::test_render_sessions_uses_session_field_names -v`
Expected: FAIL — `assert "B-42" in out` fails (panel reads `s.get("id")` → "?").

- [ ] **Step 3: Fix `_render_sessions`**

In `redrobin/tui/app.py`, replace `_render_sessions` (lines 92-104) with:

```python
def _render_sessions(state: TUIState) -> str:
    sessions = state.snapshot.get("sessions") or []
    if not sessions:
        return "(no active sessions)"
    lines = []
    for s in sessions:
        if isinstance(s, dict):
            # Session serializes as beacon_id/hostname/user/integrity (#143).
            ident = s.get("beacon_id") or "?"
            host = s.get("hostname") or "-"
            user = s.get("user") or "-"
            integ = s.get("integrity") or "-"
            lines.append(f"  {ident:<14} {host:<18} {user} [{integ}]")
        else:
            lines.append(f"  {s}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test + the full panels suite**

Run: `uv run pytest tests/unit/test_tui_panels.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" add redrobin/tui/app.py tests/unit/test_tui_panels.py
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" commit -m "fix(tui): Sessions panel reads beacon_id/hostname (was id/name/host) (#143)"
```

---

### Task 5: `.cna` emits `beacon.initial` + backfill + manifest (#143-c2)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (new emit sub + `on beacon_initial` + `beacons()` backfill + manifest `events_emitted`)
- Test: `tests/integration/test_working_memory_reducer.py` (append — daemon consumes the wire shape)

NO offline Sleep linter for the `.cna` — the emit is validated by the live-beacon smoke in Task 6. The daemon-side test here locks the wire-shape contract the `.cna` must satisfy (the `beacon.initial` schema requires all 8 fields incl. `arch` enum + integer `pid`/`ppid`, `additionalProperties:false` → a wrong shape 400s and the session never appears).

- [ ] **Step 1: Write the failing daemon-side wire-shape test**

Append to `tests/integration/test_working_memory_reducer.py`:

```python
@pytest.mark.asyncio
async def test_beacon_initial_wire_shape_creates_session(tmp_path):
    """#143: the reducer must turn a beacon.initial wire payload (the exact
    shape the .cna emits) into a Session keyed by bid, mapping
    bid->beacon_id, host->hostname, integrity_level->integrity."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-sess")
    _, task = await _start_reducer(bus, repo, "eng-sess")
    try:
        await bus.publish(
            {
                "type": "beacon.initial",
                "payload": {
                    "type": "beacon.initial",
                    "bid": "B-7", "host": "DC01", "user": "CORP\\admin",
                    "integrity_level": "High", "arch": "x64", "pid": 4321, "ppid": 700,
                },
            }
        )
        sessions = []
        for _ in range(50):
            await asyncio.sleep(0.02)
            sessions = repo.load().sessions
            if sessions:
                break
        assert any(
            s.beacon_id == "B-7" and s.hostname == "DC01" and s.integrity == "High"
            for s in sessions
        )
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run it to verify it passes ALREADY (daemon side exists) — this is a contract guard**

Run: `uv run pytest tests/integration/test_working_memory_reducer.py::test_beacon_initial_wire_shape_creates_session -v`
Expected: PASS immediately (the reducer's `_on_beacon_initial` already handles this shape). This test exists to PIN the wire contract the `.cna` must emit, and to fail loudly if the reducer mapping ever regresses. (It is not red-first because the `.cna` — the actual gap — has no Python test harness; its acceptance is the Task 6 smoke.)

- [ ] **Step 3: Add the `beacon.initial` emit sub to the `.cna`**

In `adapters/cobaltstrike/redrobin.cna`, add this sub immediately after `sub redrobin_emit_command_output { ... }` (around line 305):

```sleep
# beacon.initial — emitted on each new beacon (on beacon_initial) + backfilled
# for pre-existing beacons on load. Schema (protocol/schemas/v0.1/events/
# beacon_initial.schema.json) REQUIRES all 8 fields incl. arch enum
# (x86|x64|arm64) + integer pid/ppid, additionalProperties:false — a wrong
# shape gets a 400 (swallowed) and no session appears. binfo keys + the
# integrity " *" strip are validated by the live operator-smoke.
sub redrobin_emit_beacon_initial {
    local('$bid $host $user $arch $pid $ppid $il $event_json $body');
    $bid = $1;
    $host = binfo($bid, "computer");
    $user = binfo($bid, "user");
    # binfo("user") carries a trailing " *" in high-integrity context (see
    # redrobin_integrity_level). Strip it so the wire user is clean.
    if ($user !is $null && right($user, 2) eq " *") {
        $user = substr($user, 0, strlen($user) - 2);
    }
    $arch = binfo($bid, "arch");   # CS reports "x86" / "x64"
    $pid  = binfo($bid, "pid");
    $ppid = binfo($bid, "ppid");
    $il = redrobin_integrity_level($bid);
    # Schema requires non-empty strings + integer pid/ppid; default safely.
    if ($host is $null) { $host = "unknown"; }
    if ($user is $null) { $user = "unknown"; }
    if ($arch is $null) { $arch = "x64"; }
    if ($il is $null) { $il = "Medium"; }
    if ($pid is $null) { $pid = 0; }
    if ($ppid is $null) { $ppid = 0; }
    $event_json = '{"type":"beacon.initial",' .
                  '"bid":"' . redrobin_escape_json($bid) . '",' .
                  '"host":"' . redrobin_escape_json($host) . '",' .
                  '"user":"' . redrobin_escape_json($user) . '",' .
                  '"integrity_level":"' . redrobin_escape_json($il) . '",' .
                  '"arch":"' . redrobin_escape_json($arch) . '",' .
                  '"pid":' . $pid . ',' .
                  '"ppid":' . $ppid . '}';
    $body = redrobin_envelope($event_json);
    try {
        redrobin_http_post("/v1/events", $body);
    } catch $e {
        # Swallow silently — see #134 commit b7c7c69 rationale.
    }
}
```

- [ ] **Step 4: Add the `on beacon_initial` handler**

In `adapters/cobaltstrike/redrobin.cna`, immediately after the existing `on beacon_input { ... }` block (ends ~line 425), add:

```sleep
# New-beacon observation event (Aggressor `on beacon_initial`): $1 = bid.
on beacon_initial {
    redrobin_emit_beacon_initial($1);
}
```

- [ ] **Step 5: Backfill pre-existing beacons on load**

In `adapters/cobaltstrike/redrobin.cna`, find where the handshake completes on load (after `redrobin_handshake_with_retry()` is called during script init). Immediately AFTER the handshake call, add:

```sleep
# Backfill beacon.initial for beacons that checked in BEFORE this .cna loaded
# (on beacon_initial only fires for NEW check-ins). beacons() returns a list
# of maps with an "id" key per active beacon.
foreach $_rr_b (beacons()) {
    redrobin_emit_beacon_initial($_rr_b['id']);
}
```

(If the handshake is invoked inside an `on ready {...}` or the script body, place the backfill in the same scope right after it, so `$REDROBIN_SESSION_ID` is set and the envelope carries it.)

- [ ] **Step 6: Declare `beacon.initial` in the handshake manifest**

In `adapters/cobaltstrike/redrobin.cna`, in `redrobin_handshake_with_retry`'s `$manifest_json`, change the `events_emitted` array (line ~319-320) from:

```sleep
                     '"command_modification":false},"events_emitted":["beacon.command_input","beacon.command_output",' .
                     '"host.observation","credential.observed"],"events_consumed":["opsec.verdict"],' .
```

to:

```sleep
                     '"command_modification":false},"events_emitted":["beacon.command_input","beacon.command_output",' .
                     '"host.observation","credential.observed","beacon.initial"],"events_consumed":["opsec.verdict"],' .
```

- [ ] **Step 7: Run the daemon-side contract test (still green)**

Run: `uv run pytest tests/integration/test_working_memory_reducer.py -v`
Expected: all PASS. (The `.cna` itself is verified in Task 6's live smoke.)

- [ ] **Step 8: Commit**

```bash
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" add adapters/cobaltstrike/redrobin.cna tests/integration/test_working_memory_reducer.py
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" commit -m "feat(cna): emit beacon.initial on check-in + backfill existing beacons (#143)"
```

---

### Task 6: Full-suite regression + live-beacon smoke (acceptance)

**Files:** Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md` (add a Plan I smoke section)

- [ ] **Step 1: Full-suite regression (CI-style)**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run pytest tests/ -q`
Expected: all PASS / skipped, 0 errors. If anything UNRELATED to Plan I fails, STOP and report — do not "fix" out-of-scope tests.

- [ ] **Step 2: Add the Plan I smoke section to `test_cna_smoke.md`**

Append a `## Plan I — TUI live-state smoke (#146 / #139 / #140 / #143)` section documenting the operator steps below (match the file's existing section style + ASCII `--`, no em-dash). Steps:
1. Daemon running; `redrobin.cna` freshly unloaded+reloaded; engagement with non-empty scope; one active beacon; TUI connected (`redrobin tui`).
2. **#143 backfill:** on `.cna` load, the Sessions panel populates with the existing beacon (id + hostname + user + integrity), NOT "(no active sessions)".
3. **#143 new check-in:** spawn/relink a new beacon → it appears in Sessions within ~1s.
4. **#146 + #139 + #140 live:** run `shell whoami` then a credential-yielding command → the Working Memory panel + Inspector (Ctrl+I) update WITHOUT reconnecting: hosts/credentials/findings sections fill in live, and scope/objectives/phase show real values (not defaults).
5. **#140:** Inspector shows CREDENTIALS + FINDINGS sections with the captured data.

- [ ] **Step 3: Operator runs the live smoke (acceptance gate)**

The AI inspects daemon-side artifacts (the engagement `working_memory.json` gains a `sessions` entry; daemon log shows `POST /v1/events` `200` for `beacon.initial`). Acceptance = Sessions populates (backfill + new check-in) AND the WM panel/Inspector update live without reconnect. This is the ONLY acceptance for the Task 5 `.cna` emit.

- [ ] **Step 4: Commit the smoke spec**

```bash
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" add adapters/cobaltstrike/tests/test_cna_smoke.md
git -C "C:/Users/jcspi/Documents/dev-projects/Red-Robin" commit -m "test(cna): add Plan I TUI live-state operator-smoke (#146/#139/#140/#143)"
```

---

## Self-Review

**Spec coverage:**
- #139 (snapshot omits 9/14 fields) → Task 1 (serializer + connect-time). ✓
- #146 (frozen after connect) → Task 2 (reducer re-emit). ✓
- #140 (no findings/creds in inspector) → Task 3. ✓
- #143 (sessions empty) → Task 4 (panel field-fix, c1) + Task 5 (`.cna` `beacon.initial` emit + backfill + manifest, c2). ✓
- #147 → DEFERRED (documented in backlog.md + TODO.md Plan J; needs a new lessons-streaming path). ✓
- Live-beacon smoke (the `.cna` is the only smoke-gated piece) → Task 6. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the `.cna` step gives the full sub + the exact manifest edit. The one "passes already" step (Task 5 Step 2) is intentional + explained (it's a contract guard for the schema the `.cna` must emit, since the `.cna` has no Python harness).

**Type/name consistency:** `working_memory_to_snapshot` defined in `domain/memory.py` (Task 1) is imported by `tui_ws.py` (Task 1) and `working_memory_reducer.py` (Task 2) — same name. It is the inverse of `working_memory_from_snapshot` (verified by the round-trip test). `_dispatch` returns `bool` (Task 2) and `run()` consumes that bool — matching. Session keys `beacon_id/hostname/user/integrity` are produced by `asdict(Session)` (domain) and read by `_render_sessions` (Task 4) — matching. The `.cna` emits the 8 schema fields the reducer's `_on_beacon_initial` reads (`bid/host/user/integrity_level`) + the 3 the schema gate requires (`arch/pid/ppid`) — matching.

**Layer check:** `working_memory_to_snapshot` in `domain` is imported by `application` (reducer) and `api` (tui_ws) — both higher layers importing from `domain`, no violation. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-25-plan-i-tui-live-state.md`.

Next per the established workflow: `/auditing-plans` (audit against the codebase), then `/subagent-driven-development` (Opus 4.7, fresh subagent per task + two-stage review). Tasks 1-5 (daemon-side of 5) are unit/integration-verified; Task 6's `.cna` emit needs the live-beacon smoke.
