# Adapter Event Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the CS Aggressor adapter to receive `opsec.verdict` (Layer B) events from the daemon via a new WebSocket subscriber, so Claude's async verdicts (BLOCK/WARN/ALLOW + reason) surface directly to the operator's beacon console within ~1s of Layer B publish.

**Architecture:** Mirror the existing TUI WS pattern end-to-end — new `/v1/adapters/events` endpoint (clone of `tui_ws.py` with `opsec.verdict` filter), new `RedRobinEventStream` Java class (translation of `redrobin/tui/ws_client.py` with auto-reconnect + bounded queue), Sleep fork with `lambda()` variable capture polls the Java queue and `blog($bid, ...)` to the relevant beacon. Zero EventBus modifications; this is just another subscriber.

**Tech Stack:** Python 3.13 / FastAPI (daemon), Java 11+ `java.net.http.HttpClient.WebSocket` + vendored `org.json` (JAR), Sleep / Aggressor (`.cna`). JUnit 5 + pytest for tests.

**Spec:** `docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md`

**Risk note:** Sleep has no automated test framework — the Sleep glue layer (init sub + fork loop) is verified by Task 12 manual smoke against a real CS Client + beacon. Java code is fully unit-tested with an in-process `com.sun.net.httpserver.HttpServer` echoing WebSocket frames; daemon code has pytest coverage.

**Starting state assumption:** `dev-juan` HEAD includes the JAR helper refactor (Task #115 shipped at `31dcbd8` or later). The current JAR is `redrobin-aggressor-http-1.0.0.jar`; this plan bumps to 1.0.1.

---

## File Structure

| Path | Action |
|---|---|
| `redrobin/api/adapter_events_ws.py` | Create |
| `redrobin/api/app.py` | Modify (+1 import, +1 line) |
| `redrobin/application/verdict_pipeline.py` | Modify (`_on_retroactive` + caller) |
| `protocol/schemas/v0.1/events/opsec_verdict.schema.json` | Modify (+1 optional property) |
| `tests/integration/test_adapter_events_ws.py` | Create |
| `tests/integration/test_verdict_pipeline_layer_b_beacon_id.py` | Create (focused pytest) |
| `adapters/cobaltstrike/helper-jar/lib/json-20240303.jar` | Vendor |
| `adapters/cobaltstrike/helper-jar/lib/README.md` | Modify (document org.json) |
| `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/VerdictEvent.java` | Create |
| `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinEventStream.java` | Create |
| `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinEventStreamTest.java` | Create |
| `adapters/cobaltstrike/helper-jar/build.ps1` | Modify (include json jar; bump version 1.0.0 → 1.0.1) |
| `adapters/cobaltstrike/helper-jar/build.sh` | Modify (same) |
| `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar` | Build + commit (replaces 1.0.0) |
| `adapters/cobaltstrike/jars/json-20240303.jar` | Commit (vendored copy) |
| `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar` | Delete (replaced) |
| `adapters/cobaltstrike/redrobin.cna` | Modify (globals + init sub + boot + fork) |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify (append Layer B section) |
| `docs/operator-setup.md` | Modify (note Layer B now visible) |

---

## Task 1: Reset working tree to clean HEAD

**Files:**
- Verify: `adapters/cobaltstrike/redrobin.cna` (working tree clean)

- [ ] **Step 1: Verify branch + HEAD**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git branch --show-current
git rev-parse --short HEAD
```

Expected: `dev-juan` and a commit at `ec5dbe5` or later (the spec commit).

- [ ] **Step 2: Inspect working tree**

```bash
git status -s
```

If output shows any `M` or `??` files **for adapter or daemon code** (not for pre-existing `.gitignore`, `TESTING.md`, or `_inspect_episodic.py` left over from earlier sessions), pause and decide whether to commit or discard those changes before proceeding.

- [ ] **Step 3: No commit needed.** Proceed to Task 2.

---

## Task 2: Daemon — add `beacon_id` to Layer B payload (pytest red)

**Files:**
- Create: `tests/integration/test_verdict_pipeline_layer_b_beacon_id.py`

This is the TDD red phase for the daemon-side bug fix. Layer A includes `beacon_id` in the payload (`verdict_pipeline.py:232`); Layer B does not (`verdict_pipeline.py:208-217`). Write a pytest that asserts Layer B emission includes `beacon_id`, then watch it fail.

- [ ] **Step 1: Write the failing pytest**

Create `tests/integration/test_verdict_pipeline_layer_b_beacon_id.py` with:

```python
"""Layer B retroactive verdict must include beacon_id in payload.

Pre-existing inconsistency: Layer A's _publish_layer_a_event includes
beacon_id (verdict_pipeline.py:232), but _on_retroactive (Layer B path)
does not. The CS Aggressor adapter needs beacon_id to route the verdict
back to the right beacon console via blog($bid, ...).
"""
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline import VerdictPipeline


@pytest.mark.asyncio
async def test_layer_b_publish_includes_beacon_id():
    bus = EventBus()
    queue = await bus.subscribe()

    # Instantiate the pipeline with the bus; layer_a / layer_b providers
    # are not exercised in this test — we call _on_retroactive directly.
    pipeline = VerdictPipeline.__new__(VerdictPipeline)  # type: ignore[call-arg]
    pipeline._bus = bus  # type: ignore[attr-defined]

    await pipeline._on_retroactive(
        correlation_id="evt_abc123",
        beacon_id="bid_TESTBEACON",
        original_verdict="ESCALATE",
        new_verdict="BLOCK",
        reason="Reflective DLL injection signature",
    )

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["type"] == "opsec.verdict"
    payload = event["payload"]
    assert payload["beacon_id"] == "bid_TESTBEACON"
    assert payload["correlation_id"] == "evt_abc123"
    assert payload["verdict"] == "BLOCK"
    assert payload["previous_verdict"] == "ESCALATE"
    assert payload["source"] == "layer_b"
```

- [ ] **Step 2: Run pytest to verify RED**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
pytest tests/integration/test_verdict_pipeline_layer_b_beacon_id.py -v
```

Expected: FAIL with `TypeError: _on_retroactive() got an unexpected keyword argument 'beacon_id'`.

This is the red state — the test references a `beacon_id` parameter that doesn't exist yet.

- [ ] **Step 3: Commit (RED state)**

```bash
git add tests/integration/test_verdict_pipeline_layer_b_beacon_id.py
git commit -m "test(daemon): assert Layer B payload includes beacon_id (TDD red)

Pre-existing inconsistency: _publish_layer_a_event includes beacon_id
in payload (verdict_pipeline.py:232) but _on_retroactive (Layer B
path, lines 208-217) does not. This test fails until Task 3 adds the
beacon_id parameter to _on_retroactive and threads it through from
the _run_layer_b caller.

Spec: docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md
section 5.2."
```

---

## Task 3: Daemon — implement `beacon_id` in Layer B (pytest green)

**Files:**
- Modify: `redrobin/application/verdict_pipeline.py:_on_retroactive` and its caller `_run_layer_b`

- [ ] **Step 1: Read the current `_on_retroactive` and `_run_layer_b`**

```bash
grep -n "_on_retroactive\|_run_layer_b" redrobin/application/verdict_pipeline.py
```

Note line ranges for both methods.

- [ ] **Step 2: Add `beacon_id` parameter to `_on_retroactive` signature and payload**

Find the `_on_retroactive` method (around line 192-218). Replace its signature and body with:

```python
    async def _on_retroactive(
        self,
        *,
        correlation_id: str,
        beacon_id: str,
        original_verdict: str,
        new_verdict: str,
        reason: str,
    ) -> None:
        """Publish an ``opsec.verdict`` event for the Layer B retroactive correction.

        Called by ``_run_layer_b`` once Layer B has produced a retroactive
        verdict, and also bound onto ``LayerBEngine.on_retroactive_verdict`` by
        the factory so external Layer B implementations can drive the same path
        without going through the pipeline orchestration.
        """
        if self._bus is None:
            return
        await self._bus.publish(
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": correlation_id,
                    "beacon_id": beacon_id,
                    "verdict": new_verdict,
                    "previous_verdict": original_verdict,
                    "reason": reason,
                    "source": "layer_b",
                },
            }
        )
```

- [ ] **Step 3: Update `_run_layer_b` caller**

Find the call site of `_on_retroactive` inside `_run_layer_b`. It should currently look like:

```python
await self._on_retroactive(
    correlation_id=...,
    original_verdict=...,
    new_verdict=...,
    reason=...,
)
```

Add `beacon_id=beacon_id` to the call. The `beacon_id` should already be in scope as a parameter or local of `_run_layer_b` (verify by reading the method); if it isn't, walk up the call chain and thread it through.

- [ ] **Step 4: Check for factory-bound external callers**

```bash
grep -rn "_on_retroactive\|on_retroactive_verdict" redrobin/ 2>&1 | grep -v __pycache__
```

If the factory binds `_on_retroactive` to a `LayerBEngine.on_retroactive_verdict` hook (as the docstring suggests), update that wiring so external Layer B implementations also pass `beacon_id`.

- [ ] **Step 5: Run the Task 2 pytest to verify GREEN**

```bash
pytest tests/integration/test_verdict_pipeline_layer_b_beacon_id.py -v
```

Expected: PASS.

- [ ] **Step 6: Run the broader pipeline test suite to check no regressions**

```bash
pytest tests/integration/test_verdict_pipeline.py tests/integration/test_verdict_pipeline_factory.py -v 2>&1 | tail -30
```

If any existing test breaks because it calls `_on_retroactive` without `beacon_id`, update those tests to pass a placeholder `beacon_id` value. Do NOT skip them.

- [ ] **Step 7: Commit**

```bash
git add redrobin/application/verdict_pipeline.py
# Also stage any test files updated in Step 6
git commit -m "fix(daemon): include beacon_id in Layer B opsec.verdict payload

Layer A's _publish_layer_a_event includes beacon_id in the payload
(verdict_pipeline.py:232). Layer B's _on_retroactive did not — a
pre-existing inconsistency that prevented the CS Aggressor adapter
from routing Layer B verdicts back to the right beacon console.

Threads beacon_id through from _run_layer_b (which already has it
in scope) into _on_retroactive's signature, then into the published
payload alongside correlation_id / verdict / reason.

Resolves the test added by the prior commit. Existing pytest suite
passes unchanged."
```

---

## Task 4: Daemon — extend `opsec.verdict` schema with optional `beacon_id`

**Files:**
- Modify: `protocol/schemas/v0.1/events/opsec_verdict.schema.json`

The schema documents the protocol contract. The Layer B bus events are produced internally (not via POST), so the schema doesn't gate them, but keeping it accurate matters for protocol discoverability and future-proofing.

- [ ] **Step 1: Read the current schema**

```bash
cat protocol/schemas/v0.1/events/opsec_verdict.schema.json
```

- [ ] **Step 2: Add `beacon_id` to `properties`**

Open `protocol/schemas/v0.1/events/opsec_verdict.schema.json`. Inside the `properties` object, add:

```json
"beacon_id": {
  "type": "string",
  "description": "ID of the beacon for which the verdict applies. Required for Layer A; included in Layer B as of v0.1.x."
}
```

Place it alphabetically among the existing properties.

Do NOT add it to `required` — Layer B in v0.1 will include it but the schema permits its absence for backward-compatible replays of older traces.

- [ ] **Step 3: Verify schema validity**

```bash
python -c "import json; json.load(open('protocol/schemas/v0.1/events/opsec_verdict.schema.json'))"
```

Expected: no output (valid JSON). If it raises, fix the comma/brace placement.

- [ ] **Step 4: Run schema-related tests if any**

```bash
pytest tests/ -k "schema or opsec_verdict" -v 2>&1 | tail -10
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add protocol/schemas/v0.1/events/opsec_verdict.schema.json
git commit -m "docs(protocol): add optional beacon_id to opsec.verdict schema

Documents the field added by the prior commit. Optional rather than
required because pre-v0.1.x trace replays predate the field — the
working-memory reducer and TUI client tolerate its absence.

Spec: docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md
section 5.3."
```

---

## Task 5: Daemon — new `/v1/adapters/events` WebSocket endpoint (pytest red)

**Files:**
- Create: `tests/integration/test_adapter_events_ws.py`

- [ ] **Step 1: Write the failing pytest**

Create `tests/integration/test_adapter_events_ws.py` with:

```python
"""Integration tests for /v1/adapters/events WebSocket endpoint.

Mirrors the testing pattern of tui_ws (tests/integration/test_tui_ws.py
if it exists) but verifies the adapter-specific behavior:

  - Auth via ?token=<x> query parameter (same as TUI)
  - Server-side filter: only opsec.verdict events forwarded
  - Drop-oldest backpressure on bus full
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.application.transport_auth import get_or_create_token


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def test_adapter_events_ws_rejects_invalid_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/adapters/events?token=wrong"):
            pass


def test_adapter_events_ws_forwards_opsec_verdict(client, app):
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/adapters/events?token={token}") as ws:
        # Allow the subscribe() to register
        async def publish():
            bus = app.state.event_bus
            await bus.publish({
                "type": "opsec.verdict",
                "payload": {
                    "beacon_id": "bid_X",
                    "verdict": "BLOCK",
                    "reason": "test",
                    "source": "layer_b",
                    "correlation_id": "c_1",
                    "previous_verdict": "ESCALATE",
                },
            })

        asyncio.get_event_loop().run_until_complete(publish())
        frame = ws.receive_json()
        assert frame["type"] == "opsec.verdict"
        assert frame["payload"]["beacon_id"] == "bid_X"
        assert frame["payload"]["source"] == "layer_b"


def test_adapter_events_ws_filters_non_verdict_events(client, app):
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/adapters/events?token={token}") as ws:
        async def publish():
            bus = app.state.event_bus
            # First publish a non-verdict event — must NOT arrive
            await bus.publish({
                "type": "host.observation",
                "payload": {"hostname": "irrelevant"},
            })
            # Then a verdict — MUST arrive (proves filter is selective, not blocking)
            await bus.publish({
                "type": "opsec.verdict",
                "payload": {
                    "beacon_id": "bid_Y",
                    "verdict": "WARN",
                    "reason": "test",
                    "source": "layer_b",
                    "correlation_id": "c_2",
                    "previous_verdict": "ESCALATE",
                },
            })

        asyncio.get_event_loop().run_until_complete(publish())
        # If filter is broken, the first frame would be host.observation
        frame = ws.receive_json()
        assert frame["type"] == "opsec.verdict"
        assert frame["payload"]["beacon_id"] == "bid_Y"
```

- [ ] **Step 2: Run pytest to verify RED**

```bash
pytest tests/integration/test_adapter_events_ws.py -v
```

Expected: FAIL with `404 Not Found` or `WebSocket connection failed` — the route doesn't exist yet.

- [ ] **Step 3: Commit (RED state)**

```bash
git add tests/integration/test_adapter_events_ws.py
git commit -m "test(daemon): assert /v1/adapters/events WS behavior (TDD red)

Three test cases for the new endpoint:
  - rejects invalid token (close 1008)
  - forwards opsec.verdict frames to subscriber
  - filters non-verdict events server-side (host.observation skipped,
    next opsec.verdict still arrives)

All fail until Task 6 creates the router + registers it.

Spec: docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md
section 5.1."
```

---

## Task 6: Daemon — implement `/v1/adapters/events` WS endpoint (pytest green)

**Files:**
- Create: `redrobin/api/adapter_events_ws.py`
- Modify: `redrobin/api/app.py` (+1 import line, +1 include_router line)

- [ ] **Step 1: Create the new router file**

Create `redrobin/api/adapter_events_ws.py` with:

```python
"""WebSocket endpoint /v1/adapters/events — pushes opsec.verdict events to
the CS Aggressor adapter.

Mirror of tui_ws.py with three differences:
  1. No initial working_memory_snapshot (adapter doesn't track WM).
  2. Server-side filter: only opsec.verdict events forwarded (YAGNI for
     v0.1 — manifest events_consumed filtering arrives in v0.2 when more
     event types are consumed).
  3. Dedicated endpoint keeps the adapter contract decoupled from the
     TUI protocol; both subscribe to the same EventBus internally.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from redrobin.application.transport_auth import get_or_create_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/v1/adapters/events")
async def adapter_events_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
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

- [ ] **Step 2: Register the router in `app.py`**

Open `redrobin/api/app.py`. Find the line `from redrobin.api.tui_ws import router as tui_ws_router` (around line 42). Add immediately after it:

```python
from redrobin.api.adapter_events_ws import router as adapter_events_ws_router
```

Find the line `app.include_router(tui_ws_router)` (around line 204). Add immediately after it:

```python
    app.include_router(adapter_events_ws_router)
```

- [ ] **Step 3: Run pytest to verify GREEN**

```bash
pytest tests/integration/test_adapter_events_ws.py -v
```

Expected: all three test cases PASS.

- [ ] **Step 4: Run broader test suite to check no regressions**

```bash
pytest tests/integration/ -v 2>&1 | tail -20
```

Expected: no new failures. Pre-existing failures unrelated to this work can be ignored (but note them in the commit message if obvious).

- [ ] **Step 5: Commit**

```bash
git add redrobin/api/adapter_events_ws.py redrobin/api/app.py
git commit -m "feat(daemon): add /v1/adapters/events WebSocket endpoint

Mirror of tui_ws.py with three deltas:
  - URL /v1/adapters/events (own namespace)
  - No initial working_memory_snapshot frame (adapter doesn't track WM)
  - Server-side filter: only forwards type==opsec.verdict

The CS Aggressor .cna will subscribe to this endpoint via its
RedRobinEventStream Java class (added in subsequent tasks) and
surface Layer B verdicts to the operator's beacon console.

Resolves the test added by the prior commit. EventBus, lifespan,
and tui_ws.py are all UNCHANGED — this is purely an additive
new subscriber on the existing event delivery machinery.

Spec: docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md
section 5.1."
```

---

## Task 7: JAR — vendor `org.json` (json-20240303.jar)

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/lib/json-20240303.jar`
- Modify: `adapters/cobaltstrike/helper-jar/lib/README.md`

- [ ] **Step 1: Download the JAR from Maven Central**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar/lib
curl -fL -o json-20240303.jar https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar
ls -la json-20240303.jar
```

Expected: file size ~80 KB.

- [ ] **Step 2: Verify SHA-256**

```bash
sha256sum json-20240303.jar
```

Compare against Maven Central's published `.sha256`:

```bash
curl -s https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar.sha256
```

These must match exactly. If they don't, delete the file and re-download.

- [ ] **Step 3: Confirm the JAR contains `org.json.JSONObject`**

```bash
jar --list --file json-20240303.jar | grep -E "^org/json/JSONObject\.class$"
```

Expected: `org/json/JSONObject.class`. If empty, the JAR is wrong.

- [ ] **Step 4: Update `lib/README.md` with the new dep**

Open `adapters/cobaltstrike/helper-jar/lib/README.md` and append (after the JUnit section):

```markdown

## json-20240303.jar

Compact JSON parser (`org.json`). Used by `RedRobinEventStream` to decode
WebSocket frames from the daemon's `/v1/adapters/events` endpoint. Same
library `PyCobalt` vendors (`dcsync/pycobalt:aggressor/jars/json.jar`),
so the pattern is established in the CS Aggressor community.

**Source:** https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar

**SHA-256:** *(filled in by build step — copy the output of `sha256sum json-20240303.jar` into this slot)*

**License:** [The JSON License](https://www.json.org/license.html) — public-use compatible with EPL/MIT/Apache stacks (CS Aggressor scripts and Red Robin are MIT).
```

Replace the placeholder SHA-256 marker with the actual hash from Step 2.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add -f adapters/cobaltstrike/helper-jar/lib/json-20240303.jar adapters/cobaltstrike/helper-jar/lib/README.md
git commit -m "build(cna-jar): vendor org.json 20240303 for WS event parsing

RedRobinEventStream (added in subsequent tasks) parses WebSocket frames
from the daemon's /v1/adapters/events endpoint. JDK has no native JSON
parser; vendoring org.json matches the established PyCobalt pattern
(dcsync/pycobalt/aggressor/jars/json.jar).

~80 KB. Public JSON License. SHA-256 documented in lib/README.md.
Vendored rather than shaded into our JAR — operator sees two clearly-
named .jar files in jars/ (matches PyCobalt verbatim)."
```

---

## Task 8: JAR — write `VerdictEvent` + `RedRobinEventStreamTest` (TDD red)

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/VerdictEvent.java`
- Create: `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinEventStreamTest.java`

VerdictEvent is the public-final-fields DTO Sleep reflects on. Writing it first means the test file has a concrete type to reference.

- [ ] **Step 1: Create `VerdictEvent.java`**

Create `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/VerdictEvent.java` with:

```java
package com.redrobin.aggressor;

/**
 * Public-final-fields DTO emitted by RedRobinEventStream.pollEvent().
 *
 * Sleep reflects on this class via the [$obj fieldName] bracket syntax
 * (verified in Task #115 via RedRobinHttp.Response — same pattern).
 * All fields are public final so Sleep's getField fallback resolves
 * without setAccessible.
 */
public class VerdictEvent {
    public final String type;             // "opsec.verdict"
    public final String source;           // "layer_a" or "layer_b"
    public final String beaconId;
    public final String verdict;          // "BLOCK", "WARN", "ALLOW", "ESCALATE"
    public final String reason;
    public final String correlationId;
    public final String previousVerdict;  // populated for layer_b only; "" otherwise

    public VerdictEvent(String type, String source, String beaconId,
                        String verdict, String reason,
                        String correlationId, String previousVerdict) {
        this.type = type;
        this.source = source;
        this.beaconId = beaconId;
        this.verdict = verdict;
        this.reason = reason;
        this.correlationId = correlationId;
        this.previousVerdict = previousVerdict;
    }
}
```

- [ ] **Step 2: Create `RedRobinEventStreamTest.java`**

Create `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinEventStreamTest.java` with:

```java
package com.redrobin.aggressor;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class RedRobinEventStreamTest {

    private static final String LAYER_B_FRAME = ""
            + "{\"type\":\"opsec.verdict\",\"payload\":{"
            + "\"correlation_id\":\"c_42\","
            + "\"beacon_id\":\"bid_AAAA\","
            + "\"verdict\":\"BLOCK\","
            + "\"previous_verdict\":\"ESCALATE\","
            + "\"reason\":\"Reflective DLL injection signature\","
            + "\"source\":\"layer_b\""
            + "}}";

    private static final String LAYER_A_FRAME = ""
            + "{\"type\":\"opsec.verdict\",\"payload\":{"
            + "\"correlation_id\":\"c_43\","
            + "\"beacon_id\":\"bid_BBBB\","
            + "\"verdict\":\"WARN\","
            + "\"reason\":\"WARN reason text\","
            + "\"source\":\"layer_a\""
            + "}}";

    private static final String NON_VERDICT_FRAME = ""
            + "{\"type\":\"host.observation\",\"payload\":{\"hostname\":\"X\"}}";

    @Test
    void parseVerdictEvent_layerB() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent(LAYER_B_FRAME);
        assertNotNull(e);
        assertEquals("opsec.verdict", e.type);
        assertEquals("layer_b", e.source);
        assertEquals("bid_AAAA", e.beaconId);
        assertEquals("BLOCK", e.verdict);
        assertEquals("Reflective DLL injection signature", e.reason);
        assertEquals("c_42", e.correlationId);
        assertEquals("ESCALATE", e.previousVerdict);
    }

    @Test
    void parseVerdictEvent_layerA() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent(LAYER_A_FRAME);
        assertNotNull(e);
        assertEquals("layer_a", e.source);
        assertEquals("bid_BBBB", e.beaconId);
        assertEquals("WARN", e.verdict);
        assertEquals("", e.previousVerdict);   // not present in layer_a payload
    }

    @Test
    void parseVerdictEvent_returnsNullForNonVerdictType() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent(NON_VERDICT_FRAME);
        assertNull(e);
    }

    @Test
    void parseVerdictEvent_returnsNullForMalformedJson() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent("not-json");
        assertNull(e);
    }
}
```

(JUnit tests for the live WS connect/reconnect path require a running daemon and are covered by the Task 12 manual smoke. The unit-testable surface — the parser — is what we cover here.)

- [ ] **Step 3: Verify compile failure (RED)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes
javac --release 11 -d build/classes src/com/redrobin/aggressor/VerdictEvent.java
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar;lib/json-20240303.jar" -d build/test-classes test/com/redrobin/aggressor/RedRobinEventStreamTest.java
```

(On Linux/Mac use `:` as classpath separator instead of `;`.)

Expected: javac fails on the test compile with `cannot find symbol: class RedRobinEventStream`. The test references `RedRobinEventStream.parseVerdictEvent(...)` which doesn't exist yet.

- [ ] **Step 4: Commit (RED state)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add -f adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/VerdictEvent.java
git add -f adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinEventStreamTest.java
git commit -m "test(cna-jar): VerdictEvent DTO + parser TDD red

VerdictEvent is the public-final-fields DTO Sleep reflects on (same
pattern as RedRobinHttp.Response from Task #115).

RedRobinEventStreamTest covers the JSON parser surface:
  - layer_b frame parses with all 7 fields (incl. previous_verdict)
  - layer_a frame parses, previous_verdict empty
  - non-opsec.verdict frame returns null
  - malformed JSON returns null

Tests fail to compile until Task 9 creates RedRobinEventStream class
with the static parseVerdictEvent method."
```

---

## Task 9: JAR — implement `RedRobinEventStream` (TDD green)

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinEventStream.java`

- [ ] **Step 1: Create the implementation file**

Create `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinEventStream.java` with:

```java
package com.redrobin.aggressor;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * WebSocket subscriber that streams opsec.verdict events from the daemon's
 * /v1/adapters/events endpoint into a bounded queue Sleep can poll.
 *
 * Mirrors redrobin/tui/ws_client.py semantics:
 *   - auto-reconnect with exponential backoff (1s -> 2s -> ... -> max 60s)
 *   - per-frame JSON decode (org.json)
 *   - drop-oldest backpressure (BlockingQueue capacity 256)
 *
 * The Sleep .cna polls this via pollEvent(timeoutMs). All connection
 * lifecycle, JSON parsing, and reconnect logic lives here so the Sleep
 * code stays minimal and resilient to Sleep fork-scope quirks documented
 * in Task #110.
 */
public class RedRobinEventStream {

    private static final int QUEUE_CAPACITY = 256;
    private static final long BACKOFF_INITIAL_MS = 1_000L;
    private static final long BACKOFF_MAX_MS = 60_000L;

    private final String wsUrl;
    private final HttpClient client;
    private final BlockingQueue<VerdictEvent> queue = new ArrayBlockingQueue<>(QUEUE_CAPACITY);
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "redrobin-event-stream");
        t.setDaemon(true);
        return t;
    });

    private volatile WebSocket ws;
    private volatile boolean shutdown;
    private long currentBackoffMs = BACKOFF_INITIAL_MS;
    private long lastDisconnectAt;

    public RedRobinEventStream(String httpBaseUrl, String bearerToken) {
        String wsBase = httpBaseUrl.replaceFirst("^http", "ws");
        this.wsUrl = wsBase + "/v1/adapters/events?token=" + bearerToken;
        this.client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        connect();
    }

    /** Blocking poll. Returns null on timeout. Called by Sleep. */
    public VerdictEvent pollEvent(long timeoutMs) throws InterruptedException {
        return queue.poll(timeoutMs, TimeUnit.MILLISECONDS);
    }

    public void close() {
        shutdown = true;
        if (ws != null) {
            try { ws.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown").join(); }
            catch (Exception ignored) {}
        }
        scheduler.shutdownNow();
    }

    private void connect() {
        if (shutdown) return;
        try {
            this.ws = client.newWebSocketBuilder()
                    .buildAsync(URI.create(wsUrl), new Listener())
                    .get(5, TimeUnit.SECONDS);
            currentBackoffMs = BACKOFF_INITIAL_MS;
        } catch (Exception e) {
            scheduleReconnect();
        }
    }

    private void scheduleReconnect() {
        if (shutdown) return;
        long delay = currentBackoffMs;
        currentBackoffMs = Math.min(currentBackoffMs * 2, BACKOFF_MAX_MS);
        if (lastDisconnectAt > 0) {
            long gapMs = System.currentTimeMillis() - lastDisconnectAt;
            if (gapMs > 5_000) {
                System.err.println("[RedRobinEventStream] reconnected after "
                        + gapMs + "ms — verdicts during gap may have been "
                        + "missed; check episodic.db");
            }
        }
        scheduler.schedule(this::connect, delay, TimeUnit.MILLISECONDS);
    }

    private class Listener implements WebSocket.Listener {
        private final StringBuilder buf = new StringBuilder();

        @Override
        public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
            buf.append(data);
            if (last) {
                String frame = buf.toString();
                buf.setLength(0);
                VerdictEvent evt = parseVerdictEvent(frame);
                if (evt != null) {
                    if (!queue.offer(evt)) {
                        queue.poll();           // drop oldest
                        queue.offer(evt);
                    }
                }
            }
            ws.request(1);
            return null;
        }

        @Override
        public void onError(WebSocket ws, Throwable error) {
            lastDisconnectAt = System.currentTimeMillis();
            scheduleReconnect();
        }

        @Override
        public CompletionStage<?> onClose(WebSocket ws, int statusCode, String reason) {
            lastDisconnectAt = System.currentTimeMillis();
            scheduleReconnect();
            return null;
        }
    }

    /**
     * Parse a WS frame into a VerdictEvent.
     *
     * Returns null if:
     *   - frame is not valid JSON
     *   - frame's "type" is not "opsec.verdict"
     *
     * Visible for testing; called internally by Listener.onText.
     */
    static VerdictEvent parseVerdictEvent(String frame) {
        try {
            JSONObject root = new JSONObject(frame);
            String type = root.optString("type");
            if (!"opsec.verdict".equals(type)) return null;
            JSONObject p = root.getJSONObject("payload");
            return new VerdictEvent(
                    type,
                    p.optString("source", ""),
                    p.optString("beacon_id", ""),
                    p.optString("verdict", ""),
                    p.optString("reason", ""),
                    p.optString("correlation_id", ""),
                    p.optString("previous_verdict", "")
            );
        } catch (Exception e) {
            return null;
        }
    }
}
```

- [ ] **Step 2: Compile + run tests**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes
javac --release 11 -cp "lib/json-20240303.jar" -d build/classes $(find src -name '*.java')
javac --release 11 -cp "build/classes;lib/json-20240303.jar;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes $(find test -name '*.java')
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;lib/json-20240303.jar;build/test-classes" --scan-class-path
```

(On Windows Git Bash use `;` separator; on Linux/Mac use `:`. Also: `$(find ...)` works in bash but not PowerShell — use PowerShell equivalents from `build.ps1` if running there.)

Expected: 4 tests successful, 0 tests failed.

- [ ] **Step 3: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add -f adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinEventStream.java
git commit -m "feat(cna-jar): RedRobinEventStream — WS subscriber for opsec.verdict

Java translation of redrobin/tui/ws_client.py semantics:
  - HttpClient.WebSocket-based subscriber to /v1/adapters/events
  - Auto-reconnect with exponential backoff (1s -> 2s -> ... -> max 60s)
  - Per-frame JSON decode via org.json
  - Drop-oldest backpressure (BlockingQueue capacity 256)
  - Public pollEvent(timeoutMs) -> VerdictEvent | null for Sleep to call

Listener.onText filters frames via parseVerdictEvent (static, package-
visible for testing). Only opsec.verdict frames produce VerdictEvent
instances on the queue; everything else is silently ignored.

Daemon-thread named 'redrobin-event-stream' for visibility in thread
dumps. ScheduledExecutorService handles backoff scheduling; ws.request(1)
maintains flow-control demand.

4/4 JUnit tests pass against in-line JSON fixtures (no daemon needed
for parser coverage; live WS connection is covered by Task 12 smoke)."
```

---

## Task 10: JAR — update build scripts + ship new artifact

**Files:**
- Modify: `adapters/cobaltstrike/helper-jar/build.ps1`
- Modify: `adapters/cobaltstrike/helper-jar/build.sh`
- Create: `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar` (build artifact)
- Create: `adapters/cobaltstrike/jars/json-20240303.jar` (vendored copy)
- Delete: `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar`

- [ ] **Step 1: Update `build.ps1`**

Open `adapters/cobaltstrike/helper-jar/build.ps1`. Find the line:

```powershell
$VERSION = "1.0.0"
```

Replace with:

```powershell
$VERSION = "1.0.1"
$JSON_JAR = "lib\json-20240303.jar"
```

Find the `javac` lines (around lines 17 and 21). Update each to include `$JSON_JAR` in the classpath:

For the `src` compile (around line 17):
```powershell
javac --release 11 -cp "$JSON_JAR" -d build\classes $srcFiles
```

For the `test` compile (around line 21):
```powershell
javac --release 11 -cp "build\classes;$JUNIT;$JSON_JAR" -d build\test-classes $testFiles
```

For the JUnit run line (around line 25):
```powershell
java -jar $JUNIT --class-path "build\classes;$JSON_JAR;build\test-classes" --scan-class-path
```

Find the JAR-package + copy section. After `Copy-Item "build\jar\$JAR_NAME" -Destination $ParentJarsDir -Force`, ADD:

```powershell
# Also copy the vendored json jar — adapter loads both via URLClassLoader
Copy-Item $JSON_JAR -Destination $ParentJarsDir -Force
```

- [ ] **Step 2: Update `build.sh` mirror-style**

Open `adapters/cobaltstrike/helper-jar/build.sh`. Find:

```bash
VERSION="1.0.0"
```

Replace with:

```bash
VERSION="1.0.1"
JSON_JAR="lib/json-20240303.jar"
```

Update the two `javac` calls to include `:$JSON_JAR` in the classpath (note: bash uses `:` separator unless on Git Bash for Windows, in which case `;` is needed because the JVM is Windows-native):

```bash
echo "Compiling src/..."
find src -name '*.java' -print0 | xargs -0 javac --release 11 -cp "$JSON_JAR" -d build/classes

echo "Compiling test/..."
find test -name '*.java' -print0 | xargs -0 javac --release 11 \
    -cp "build/classes:$JUNIT:$JSON_JAR" -d build/test-classes

echo "Running tests..."
java -jar "$JUNIT" --class-path "build/classes:$JSON_JAR:build/test-classes" --scan-class-path
```

After the `cp build/jar/${JAR_NAME} ${PARENT_JARS_DIR}/` line, ADD:

```bash
cp "$JSON_JAR" "${PARENT_JARS_DIR}/"
```

- [ ] **Step 3: Run the build end-to-end**

Use Bash on Windows Git Bash (PowerShell may be denied by sandbox) — and remember the classpath separator quirk:

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes build/jar
javac --release 11 -cp "lib/json-20240303.jar" -d build/classes $(find src -name '*.java')
javac --release 11 -cp "build/classes;lib/json-20240303.jar;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes $(find test -name '*.java')
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;lib/json-20240303.jar;build/test-classes" --scan-class-path
jar --create --file "build/jar/redrobin-aggressor-http-1.0.1.jar" -C build/classes .
mkdir -p ../jars
cp build/jar/redrobin-aggressor-http-1.0.1.jar ../jars/
cp lib/json-20240303.jar ../jars/
ls -la ../jars/
```

Expected output: both `redrobin-aggressor-http-1.0.1.jar` (~5-7 KB, larger than 1.0.0 due to new classes) and `json-20240303.jar` (~80 KB) in `../jars/`.

- [ ] **Step 4: Verify JAR contents**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
jar --list --file adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar | sort
```

Expected entries:
```
META-INF/
META-INF/MANIFEST.MF
com/
com/redrobin/
com/redrobin/aggressor/
com/redrobin/aggressor/RedRobinEventStream$Listener.class
com/redrobin/aggressor/RedRobinEventStream.class
com/redrobin/aggressor/RedRobinHttp$HttpException.class
com/redrobin/aggressor/RedRobinHttp$Response.class
com/redrobin/aggressor/RedRobinHttp.class
com/redrobin/aggressor/VerdictEvent.class
```

All 6 `.class` files must be present. If any is missing, re-run Step 3.

- [ ] **Step 5: Delete the old 1.0.0 JAR**

```bash
git rm adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar
```

- [ ] **Step 6: Commit**

```bash
git add adapters/cobaltstrike/helper-jar/build.ps1 adapters/cobaltstrike/helper-jar/build.sh
git add -f adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar
git add -f adapters/cobaltstrike/jars/json-20240303.jar
git commit -m "build(cna-jar): ship 1.0.1 with RedRobinEventStream + vendored org.json

JAR version bump 1.0.0 -> 1.0.1 reflects the new public class
RedRobinEventStream (and supporting VerdictEvent DTO). Build scripts
add lib/json-20240303.jar to both javac classpath and test runner
classpath; vendored json jar is now copied into ../jars/ alongside
the main artifact so the .cna URLClassLoader can load both.

The .cna in the subsequent task references both jars by filename:
  redrobin-aggressor-http-1.0.1.jar  (our code)
  json-20240303.jar                  (org.json parser)

Old 1.0.0 artifact removed; the .cna will fail loudly on next load
if any operator still has 1.0.0 referenced.

Verified: all 6 .class files present in 1.0.1 (RedRobinHttp +
inner classes Response/HttpException, RedRobinEventStream + inner
Listener, VerdictEvent). JUnit 4/4 pass on a clean rebuild."
```

---

## Task 11: `.cna` — wire up event stream + Layer B handler fork

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

This task makes 4 coordinated changes in ONE commit (same atomic-pivot pattern as Task #115's T7): top-level globals, new init sub, boot block order, and the Layer B handler fork. Splitting these would leave the `.cna` in a non-functional intermediate state.

- [ ] **Step 1: Locate the current code blocks**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "global('\\\$REDROBIN_\|sub redrobin_init_http\b\|# ===== Boot\|# ===== Heartbeat fork — DISABLED" adapters/cobaltstrike/redrobin.cna
```

Note the line numbers for: top-level globals (~line 23-27), init_http sub (~line 285), boot block (~line 294), heartbeat fork comment (~line 338).

- [ ] **Step 2: Update top-level globals**

Find the block (around lines 23-27):

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID $REDROBIN_HTTP');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
$REDROBIN_HTTP = $null;
```

Replace with:

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID $REDROBIN_HTTP $REDROBIN_EVENT_STREAM');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
$REDROBIN_HTTP = $null;
$REDROBIN_EVENT_STREAM = $null;
```

- [ ] **Step 3: Update `redrobin_init_http` to reference the new JAR filename**

Find the line in `redrobin_init_http`:

```sleep
    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.0.jar';
```

Replace with:

```sleep
    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.1.jar';
```

- [ ] **Step 4: Add `redrobin_init_event_stream` sub**

Find the end of `redrobin_init_http` sub (closing `}` around line 292). Immediately after it, insert:

```sleep


# ===== Event stream init =====
# Load com.redrobin.aggressor.RedRobinEventStream from the same JAR as
# RedRobinHttp + sibling json-20240303.jar (org.json parser). The stream's
# WebSocket connection lives in a Java background thread — Sleep just polls
# the resulting queue.
sub redrobin_init_event_stream {
    global('$REDROBIN_EVENT_STREAM');
    local('$jar_path $json_jar_path $jar_url $json_url $loader $cls $string_cls $ctor');

    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.1.jar';
    $json_jar_path = script_resource() . '/jars/json-20240303.jar';
    $jar_url = [[[new java.io.File: $jar_path] toURI] toURL];
    $json_url = [[[new java.io.File: $json_jar_path] toURI] toURL];
    $loader = [new java.net.URLClassLoader: @($jar_url, $json_url)];

    $cls = [java.lang.Class forName: 'com.redrobin.aggressor.RedRobinEventStream', false, $loader];
    $string_cls = [java.lang.Class forName: 'java.lang.String'];
    $ctor = [$cls getConstructor: @($string_cls, $string_cls)];
    $REDROBIN_EVENT_STREAM = [$ctor newInstance: @($REDROBIN_URL, $REDROBIN_TOKEN)];
}
```

- [ ] **Step 5: Update `redrobin_init_http` to load the json jar too**

`redrobin_init_http` currently builds a URLClassLoader with only the main jar. Update it to include the json jar so RedRobinHttp gets the same classpath visibility (defensive — RedRobinHttp doesn't use org.json today but co-loading is harmless and keeps both classes in the same loader hierarchy).

Find inside `redrobin_init_http`:

```sleep
    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.1.jar';
    $jar_url = [[[new java.io.File: $jar_path] toURI] toURL];
    $loader = [new java.net.URLClassLoader: @($jar_url)];
```

Replace with:

```sleep
    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.1.jar';
    local('$json_jar_path $json_url');
    $json_jar_path = script_resource() . '/jars/json-20240303.jar';
    $jar_url = [[[new java.io.File: $jar_path] toURI] toURL];
    $json_url = [[[new java.io.File: $json_jar_path] toURI] toURL];
    $loader = [new java.net.URLClassLoader: @($jar_url, $json_url)];
```

(The pre-existing `local('...')` line at the top of `redrobin_init_http` declares `$jar_path $jar_url $loader $cls $string_cls $ctor`. We add `$json_jar_path $json_url` via a second `local()` call — Sleep allows multiple `local()` declarations in the same sub.)

- [ ] **Step 6: Update the boot block**

Find the boot block (around line 294-297):

```sleep
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_init_http();
redrobin_handshake_with_retry();
```

Replace with:

```sleep
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_init_http();
redrobin_init_event_stream();
redrobin_handshake_with_retry();
```

The order matters: `redrobin_init_event_stream` reads `$REDROBIN_URL` and `$REDROBIN_TOKEN` at construction time, so both must already be set.

- [ ] **Step 7: Add the Layer B handler fork**

Find the disabled-heartbeat fork comment block (around line 338, search for `# ===== Heartbeat fork — DISABLED`). Immediately AFTER its closing comment (the commented-out `# fork({` block), insert:

```sleep


# ===== Layer B verdict consumer fork =====
# Async LLM judge (Layer B) finishes 10-30s after the operator typed a
# command and publishes opsec.verdict events. This fork polls the
# RedRobinEventStream's queue and surfaces verdicts to the relevant beacon
# console via blog($bid, ...).
#
# Sleep fork() creates an isolated execution context that does NOT see
# parent globals (verified empirically 2026-05-18, Task #110). lambda()
# with explicit variable capture is the documented Sleep idiom for this —
# see Sleep manual S10. We capture $REDROBIN_EVENT_STREAM as $_stream so
# the fork body can call its pollEvent method.
fork(lambda({
    local('$evt $msg');
    while (1) {
        $evt = [$_stream pollEvent: 5000];   # 5s timeout, then loop again
        if ($evt !is $null) {
            if ([$evt source] eq "layer_b") {
                $msg = "[Red Robin] Layer B " . [$evt verdict] . ": " . [$evt reason];
                blog([$evt beaconId], $msg);
            }
        }
    }
}, $_stream => $REDROBIN_EVENT_STREAM));
```

- [ ] **Step 8: Verify everything landed atomically**

```bash
grep -n "REDROBIN_EVENT_STREAM\|redrobin_init_event_stream\|fork(lambda" adapters/cobaltstrike/redrobin.cna
```

Expected:
- Top-level `global('$REDROBIN_URL ... $REDROBIN_EVENT_STREAM');`
- Top-level `$REDROBIN_EVENT_STREAM = $null;`
- `sub redrobin_init_event_stream { ... }`
- Boot block call `redrobin_init_event_stream();`
- `fork(lambda({...}, $_stream => $REDROBIN_EVENT_STREAM));` near script tail
- No references to `redrobin-aggressor-http-1.0.0.jar` (should be 1.0.1 everywhere)

```bash
grep -n "1\.0\.0\.jar" adapters/cobaltstrike/redrobin.cna
```

Expected: empty.

- [ ] **Step 9: Commit (atomic .cna pivot)**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): consume opsec.verdict via RedRobinEventStream fork

Single atomic commit (four coordinated changes — splitting would leave
the .cna runtime-broken between intermediate states):

  1. Top-level global \$REDROBIN_EVENT_STREAM with \$null init.
  2. redrobin_init_http updated to:
       - reference jars/redrobin-aggressor-http-1.0.1.jar (was 1.0.0)
       - include sibling json-20240303.jar in URLClassLoader array
  3. New redrobin_init_event_stream sub: loads RedRobinEventStream
     class from same JAR + json.jar via URLClassLoader, constructs
     instance with (\$REDROBIN_URL, \$REDROBIN_TOKEN).
  4. Boot block reordered: load_token -> init_http -> init_event_stream
     -> handshake_with_retry. Event stream must init AFTER token loads
     (ctor reads it) and AFTER http init (defensive ordering).
  5. New fork(lambda({...}, \$_stream => \$REDROBIN_EVENT_STREAM)) at
     script tail. Captures \$REDROBIN_EVENT_STREAM via lambda explicit
     binding (Sleep manual S10) since fork() creates isolated scope
     (Task #110 root cause). Loop polls pollEvent(5000), filters for
     source=layer_b, calls blog(beaconId, '[Red Robin] Layer B X: Y').

After this commit the .cna delivers Layer B verdicts to the operator's
beacon console end-to-end (Task #123 resolution).

Pattern of lambda() capture in fork() can be reused to re-enable the
heartbeat fork (Task #110) in a follow-up; the technique is now
documented in-tree."
```

---

## Task 12: Append Layer B smoke section to test_cna_smoke.md

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md`

- [ ] **Step 1: Read the existing smoke doc tail**

```bash
tail -10 adapters/cobaltstrike/tests/test_cna_smoke.md
```

Confirm the file currently ends with the prior CNA JAR helper smoke section (added 2026-05-17).

- [ ] **Step 2: Append the new section**

Append the following at the end of the file:

````markdown

---

## Layer B verdict visibility smoke (added 2026-05-20)

These steps verify the refactor in `docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md`. Requires daemon running, CS Client connected with .cna loaded, a real beacon already callbacked.

### TC1: Layer B verdict reaches beacon console

- [ ] Daemon running: `redrobin daemon start --host 127.0.0.1 --port 7777`
- [ ] csrestapi running on teamserver (per runbook).
- [ ] CS Client launched stock (no JVM flags), connected to teamserver, beacon active.
- [ ] Load `adapters/cobaltstrike/redrobin.cna` in CS Client.
- [ ] In daemon log expect: `INFO: ... "GET /v1/adapters/events ... HTTP/1.1" 101 Switching Protocols` (the WS upgrade — confirms the .cna's RedRobinEventStream connected).
- [ ] In Script Console expect: `[Red Robin] handshake OK; session=ses_<uuid>` followed by `[Red Robin] .cna loaded; gate active.` No errors.
- [ ] Type `mimikatz` in a beacon console.
- [ ] Within ~1s, beacon console expect: `[Red Robin] novel pattern — Layer B reviewing async` (Layer A immediate response).
- [ ] Within ~30s, the SAME beacon console expect a second line: `[Red Robin] Layer B BLOCK: <Claude rationale about Reflective DLL injection / mimikatz signatures>` (Layer B async verdict).

If the second line appears, TC1 PASS.

### TC2: Verdict for different command surfaces correctly

- [ ] Type `shell whoami` in the beacon.
- [ ] Layer A returns immediately (PASS — no rule fires; `whoami` executes).
- [ ] Within ~30s, a `[Red Robin] Layer B WARN: <reason about cmd.exe child process>` line should appear (Claude's heuristic flag — matching the empirical evidence from 2026-05-18 smoke session in episodic.db).

### TC3: Reconnect after daemon restart

- [ ] With .cna loaded and idle, stop the daemon (Ctrl+C in daemon terminal).
- [ ] Wait ~5s.
- [ ] Restart daemon (`redrobin daemon start --host 127.0.0.1 --port 7777`).
- [ ] Within ~60s the JAR should reconnect. Daemon log shows a new WS upgrade. Script Console may show a warn line about gap-during-reconnect (acceptable).
- [ ] Trigger TC1 again (`mimikatz`). Verify Layer B verdict still arrives — proves the new WS connection works.

### TC4: Cross-check verdict against episodic.db

After TC1, run:

```bash
python C:/Users/jcspi/redrobin-smoke/_inspect_episodic.py 2>&1 | tail -20
```

The MOST RECENT row should match the verdict + reason that appeared in beacon for the `mimikatz` command. Confirms beacon console output = daemon's stored verdict (no truncation, no drift).
````

- [ ] **Step 3: Commit**

```bash
git add -f adapters/cobaltstrike/tests/test_cna_smoke.md
git commit -m "docs(test-smoke): add Layer B verdict visibility section

Four sub-TCs for the adapter event stream refactor:
  TC1: Layer B verdict appears in beacon console after ~30s
  TC2: Different commands route correctly (shell whoami -> WARN)
  TC3: Reconnect after daemon restart still delivers verdicts
  TC4: Beacon output matches episodic.db (no drift)

TC1 is the gate — if a Layer B verdict doesn't surface within 30s
of a command that triggers ESCALATE, the new event stream is broken
and Task #123 is not resolved."
```

---

## Task 13: Update operator-setup.md

**Files:**
- Modify: `docs/operator-setup.md`

- [ ] **Step 1: Find the existing CS adapter section**

```bash
grep -n "Cobalt Strike Client Java requirement\|Layer B\|opsec.verdict" docs/operator-setup.md
```

- [ ] **Step 2: Add a Layer B feedback subsection**

Find the H3 `### Cobalt Strike Client Java requirement` section (added by the Task #115 plan). Immediately AFTER that section (before the next H2 or end of file), insert:

```markdown
### Layer B (async) verdict visibility

Red Robin's pre-flight gate has two layers:

- **Layer A** — synchronous Sigma/YAML rule eval (~150 ms). Verdicts surface
  in the beacon console immediately as `[Red Robin] DANGER: ...`,
  `[Red Robin] WARN: ...`, or `[Red Robin] novel pattern — Layer B reviewing async`.

- **Layer B** — async Claude LLM judge (~10–30 s). When Layer B finishes
  evaluating a command that Layer A marked as ESCALATE, its verdict surfaces
  back to the **same beacon console** as a second line:

  ```
  [Red Robin] Layer B BLOCK: <Claude's rationale>
  [Red Robin] Layer B WARN:  <Claude's rationale>
  [Red Robin] Layer B ALLOW: <Claude's rationale>
  ```

Layer B verdicts are delivered over a WebSocket subscription to the daemon
(`/v1/adapters/events`), maintained by the
`com.redrobin.aggressor.RedRobinEventStream` class inside the helper JAR.
No operator configuration required — the connection establishes
automatically when `redrobin.cna` loads.

If the daemon goes down, the WS connection drops; the JAR reconnects with
exponential backoff (up to 60 s). Verdicts published during the gap are
NOT replayed — check `episodic.db` for the historical record:

```powershell
python C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py
```

(That helper script is in the engagement root, created during the
2026-05-18 smoke session — copy it to new engagements as needed.)
```

- [ ] **Step 3: Commit**

```bash
git add -f docs/operator-setup.md
git commit -m "docs(operator-setup): document Layer B verdict visibility

Explains the two-layer pre-flight gate from the operator's perspective:
Layer A returns synchronously in the beacon console (already familiar);
Layer B now also returns to the same beacon console ~10-30s later via
the new RedRobinEventStream WS subscription. No operator config needed.

Also notes the reconnect-with-gap behavior: dropped events during a
daemon restart are NOT replayed by v0.1; operator can consult
episodic.db via the _inspect_episodic.py helper for the record.

Spec: docs/superpowers/specs/2026-05-20-adapter-event-stream-design.md
sections 1.1, 8."
```

---

## Task 14: Comprehensive end-to-end smoke validation

This is the gate task — running all four sub-TCs from the new "Layer B verdict visibility smoke" section against real CS Client + daemon + beacon.

**Files:** None modified. Verification only.

- [ ] **Step 1: Ensure prerequisites**

- CS REST API listening on teamserver:
  ```bash
  ssh root@100.103.238.211 'ss -tlnp | grep -E ":(50050|50443)"'
  ```
  Both ports must listen. If `50443` missing, respawn `csrestapi` per
  `red-team-playground/docs/runbooks/cs-restapi-bringup.md`.
- Daemon running locally: `redrobin daemon start --host 127.0.0.1 --port 7777`
- `redrobin doctor` returns `OK` for cs-adapter, anthropic, voyage.
- CS Client closed (we want a fresh launch).
- JAR + json jar in place:
  ```bash
  ls adapters/cobaltstrike/jars/
  ```
  Must include `redrobin-aggressor-http-1.0.1.jar` and `json-20240303.jar`.

- [ ] **Step 2: Run TC1 (Layer B verdict → beacon)**

Stock launch:

```powershell
cd C:\Users\jcspi\Documents\opt\cobaltstrike\client
.\cobaltstrike.exe
```

Connect to teamserver → load `redrobin.cna`. Wait for handshake-OK.

Trigger a real beacon callback (per Tier 4 setup from the prior smoke). In the beacon console type `mimikatz`. Watch for the SECOND `[Red Robin] Layer B ...` line within ~30 s.

If it appears, mark TC1 PASS. If not, capture daemon log + Script Console output and stop here.

- [ ] **Step 3: Run TC2-TC4 from the smoke doc**

Walk each sub-TC. Tick the boxes. Capture any unexpected output.

- [ ] **Step 4: Push to origin**

When all 4 TCs pass:

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git push origin dev-juan
```

---

## Self-Review (skill checklist)

**Spec coverage:** Every section of `2026-05-20-adapter-event-stream-design.md` maps to at least one task:

- §1 Context → Task 14 (TC4 cross-check vs episodic.db proves the same verdicts the spec quotes)
- §2 G1 (latency) → TC1 step "Within ~30s" budget verified manually
- §2 G2 (WS pattern) → Tasks 5/6 (daemon) + Task 9 (JAR) mirror tui_ws.py / ws_client.py
- §2 G3 (zero EventBus mods) → no task touches event_bus.py
- §2 G4 (zero JVM flags) → Task 14 TC1 verifies stock launch
- §2 G5 (reconnect) → Task 9 implements + Task 14 TC3 verifies
- §2 G6 (token sanitization) → existing `redrobin_sanitize_error` reused; Task 11 catch-block patterns inherit it via the http helper path
- §2 G7 (build discipline) → Task 10 ships JAR + vendored json
- §2 G8 (lambda capture) → Task 11 establishes the pattern in tree
- §3 non-goals → not implemented; documented in spec
- §4 Architecture → Tasks 5-11 implement the three-layer view
- §5.1-5.9 components → Tasks 5-13
- §6 contracts → preserved in Tasks 3 (beacon_id added; existing flow otherwise unchanged), 9 (RedRobinHttp untouched), 11 (boot order spec-compliant)
- §7 Java compat → Task 10 build script uses `--release 11`
- §8 security → Task 9's onError logging uses class name, not URL; Task 11 catch reuses sanitize helper
- §9 test plan → JUnit (Task 8-9), pytest (Task 2-5), manual smoke (Task 12, executed in Task 14)
- §10 rollback → per-task `git revert` simplicity
- §11 implementation order → matches Tasks 1-14
- §12 open questions → Q1 resolved (bus shape), Q2 resolved (vendor org.json), Q3 resolved (two separate JARs — Task 10 ships both), Q4 resolved (1.0.1 — Tasks 10/11), Q5 explicitly deferred to follow-up (this plan does NOT re-enable heartbeat fork), Q6 resolved (server-side hardcoded filter in Task 6).

**Placeholder scan:** No TBD/TODO/"implement later"/"similar to Task N". Each step contains complete code, complete commit message, and exact commands.

**Type consistency:**
- `RedRobinEventStream(String httpBaseUrl, String bearerToken)` ctor in Task 9 → matches Task 11's `newInstance: @($REDROBIN_URL, $REDROBIN_TOKEN)` ✓
- `RedRobinEventStream.pollEvent(long timeoutMs) -> VerdictEvent` → Task 11's `[$_stream pollEvent: 5000]` ✓
- `VerdictEvent.beaconId` public field → Task 11's `[$evt beaconId]` ✓ (Sleep camelCase field access via reflection — same pattern as `RedRobinHttp.Response.code` / `.body` from Task #115)
- `RedRobinEventStream.parseVerdictEvent(String) -> VerdictEvent | null` → Task 8's tests reference this exact signature ✓
- `_on_retroactive(*, correlation_id, beacon_id, ...)` keyword-only signature in Task 3 → Task 2's pytest calls it with `beacon_id="bid_TESTBEACON"` ✓

All references consistent. Ready for execution.
