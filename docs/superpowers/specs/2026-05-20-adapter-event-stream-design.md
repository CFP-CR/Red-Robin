# Adapter Event Stream Design Spec

**Date:** 2026-05-20
**Branch target:** `dev-juan`
**Resolves:** Task #123 — wire `.cna` to receive `opsec.verdict` (Layer B) events
**Also resolves:** Task #110 — Sleep fork scope (the `lambda()` capture pattern this spec establishes also unblocks heartbeat fork re-enable)

## 1. Context

### 1.1 Why this spec exists

The CS Aggressor adapter (`adapters/cobaltstrike/redrobin.cna`) declares in its manifest:

```toml
events_consumed = ["opsec.verdict"]
```

…but has **zero code** to actually receive those events. Empirical confirmation during T13 smoke 2026-05-18: Layer B (the async Claude judge) generates excellent verdicts with detailed Reflective DLL / EDR rationales and persists them to `episodic.db`. Two examples from a smoke session:

| Layer | Time | Command | Verdict | Reason (truncated) |
|---|---|---|---|---|
| A sync | 17:48:24 | `mimikatz` | ESCALATE | "novel pattern" |
| **B async** (+12s) | 17:48:36 | (mimikatz) | **BLOCK** | "Running the built-in mimikatz command in Cobalt Strike invokes a well-known, heavily signatured inline Reflective DLL injection…" |
| A sync | 17:49:04 | `shell` | ESCALATE | "novel pattern" |
| **B async** (+10s) | 17:49:14 | (shell whoami) | **WARN** | "Spawning 'whoami' via Beacon's 'shell' command forks cmd.exe as a child of the Beacon process, which is a well-known EDR/Sysmon detection tripwire…" |

Neither Layer B verdict reached the operator's beacon console. They sit in `episodic.db` invisible to the operator who needs them.

### 1.2 Why this is a v0.1.x BLOCKER

Layer B is the load-bearing differentiator of Red Robin's pre-flight gate architecture. Layer A catches known-bad patterns (Sigma/YAML); Layer B handles the long tail of "novel pattern" cases via LLM judgment. **An operator who can't see Layer B verdicts effectively has no Layer B.** Ship v0.1 without this and the demo at DEF CON RTV 2026-05-31 reduces to "Layer A worked, trust us about Layer B".

### 1.3 Existing architecture (verified by reading code)

Red Robin's event delivery pipeline is **already** production-grade for the TUI consumer. The pattern this spec mirrors:

```
EventBus (event_bus.py:9-32, multi-subscriber fan-out, drop-oldest on full queue)
    │ publish({"type": ..., "payload": ...})
    ├─→ tui_ws.py        ─ websocket /v1/tui          → TUI Python client
    ├─→ working_memory_reducer.py (in-process)
    └─→ adapter_events_ws.py NEW ─ /v1/adapters/events → .cna RedRobinEventStream NEW
```

The verified components we **REUSE without modification**:

- `EventBus` — multi-subscriber, drop-oldest backpressure, async-safe (`event_bus.py:9`)
- Layer B publish path — already publishes to `EventBus` (`verdict_pipeline.py:198-218`)
- WS auth via `?token=<x>` query — established convention (`tui_ws.py:21-23`)
- Bus shape `{"type": ..., "payload": ...}` — what TUI receives (`tui_ws.py:39`)
- JAR class loading via `URLClassLoader` — established by Task #115 (commits `a4f935c..6d3dbf3`)
- Sleep `lambda()` for fork variable capture — documented in `redrobin.cna:352`

### 1.4 Components we ADD (minimal delta)

| File | Action | Why this addition |
|---|---|---|
| `redrobin/api/adapter_events_ws.py` | NEW | New WS endpoint mirroring `tui_ws.py` with server-side `opsec.verdict` filter |
| `redrobin/api/app.py` | +1 line | Register the new router |
| `redrobin/application/verdict_pipeline.py` | Modify | Add `beacon_id` to Layer B payload (pre-existing inconsistency vs Layer A) |
| `protocol/schemas/v0.1/events/opsec_verdict.schema.json` | Modify | Document optional `beacon_id` field |
| `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinEventStream.java` | NEW | Java WS client mirror of `tui/ws_client.py` |
| `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/VerdictEvent.java` | NEW | Public-final-fields DTO Sleep reflects on |
| `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinEventStreamTest.java` | NEW | JUnit 5 tests with in-process WS server |
| `adapters/cobaltstrike/helper-jar/lib/json-20240303.jar` | Vendor | JSON parsing (org.json, same library PyCobalt vendors) |
| `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar` | New build | JAR bumped to 1.0.1 (added 2nd class) |
| `adapters/cobaltstrike/redrobin.cna` | Modify | `redrobin_init_event_stream` sub + `fork(lambda({...}, ...))` |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify | New TC for Layer B verdict visibility |
| `docs/operator-setup.md` | Modify | Note Layer B feedback now visible in beacon console |

**Net new public surface:** 1 WS endpoint, 1 Java class hierarchy (`RedRobinEventStream` + `VerdictEvent`), 1 Sleep init sub, 1 Sleep fork.

**Architectural changes:** zero. Every addition extends an existing pattern.

## 2. Goals

| ID | Goal |
|---|---|
| **G1** | When Layer B finishes evaluating a command, the verdict (BLOCK/WARN/ALLOW + reason) appears in the operator's beacon console within ~1s of Layer B publish. |
| **G2** | Adapter consumes events via WebSocket subscription — same delivery mechanism as TUI (single source of truth, single transport pattern). |
| **G3** | Zero modification to `EventBus`, `verdict_pipeline` flow, or daemon lifespan. Only ADDITIVE changes (one new endpoint, one payload field). |
| **G4** | Stock CS Client launch preserved — no new JVM flags. (Maintains the Task #115 win.) |
| **G5** | Reconnect-resilient: if the WS disconnects, the adapter reconnects with exponential backoff. Lost events during gaps are logged (not silently dropped). |
| **G6** | Token sanitization preserved: bearer token never appears in operator-facing error messages (via existing `redrobin_sanitize_error`). |
| **G7** | JAR shipping discipline preserved: pre-built `.jar` committed to `adapters/cobaltstrike/jars/`, build is reproducible via `build.sh` / `build.ps1` with stock `javac` + `jar` + vendored libs. |
| **G8** | Pattern establishes `lambda()` Sleep fork variable capture — unblocks Task #110 (heartbeat fork re-enable) by example. |

## 3. Non-goals

| # | What we are NOT doing | Why |
|---|---|---|
| 1 | Generic events_consumed manifest filtering server-side | YAGNI for v0.1 — only one consumed type. Daemon hardcodes `type == "opsec.verdict"` filter. Generalize when v0.2 adds more types. |
| 2 | Resume-from-sequence after disconnect (`?since_seq=<n>`) | YAGNI for v0.1. Acceptable lossy on reconnect — log warn so operator knows to consult `episodic.db`. |
| 3 | Per-bid delivery routing | EventBus is global fan-out; .cna filters client-side by `payload.beacon_id` for blog() target. |
| 4 | Surfacing Layer A verdicts via the new endpoint | Layer A is already returned synchronously by `/v1/verdict/quick` and the .cna already calls `blog`/`berror` from `redrobin_gate`. No need to duplicate via the event stream. We filter Layer A events out client-side. |
| 5 | Reusing `/v1/tui` for the adapter | Mixes consumer concerns. TUI evolves its protocol independently of adapter contract. Dedicated `/v1/adapters/events` keeps boundaries clean. |
| 6 | Generic JSON-RPC or GraphQL | Way over-scoped for "send 1 event type to 1 consumer". |
| 7 | WebSocket auth refresh mid-session | Daemon token doesn't rotate during a session. Re-handshake on disconnect picks up any new token. |

## 4. Architecture

### 4.1 Layered view

```
┌────────────────────────────────────────────────────────────────┐
│  DAEMON (Python / FastAPI)                                     │
│                                                                │
│  verdict_pipeline._on_retroactive()                            │
│      adds beacon_id to payload (this spec)                     │
│      └─→ EventBus.publish({type: "opsec.verdict",              │
│                            payload: {beacon_id, verdict, ...}})│
│            ↓                                                   │
│  EventBus fans out to all subscribers (unchanged)              │
│            ↓                                                   │
│  adapter_events_ws.py (NEW)                                    │
│    @router.websocket("/v1/adapters/events")                    │
│      validates token, subscribes to bus                        │
│      filters: type == "opsec.verdict"                          │
│      forwards JSON to socket                                   │
└────────────────────────────────────────────────────────────────┘
                          │ WebSocket frame (JSON)
                          ↓
┌────────────────────────────────────────────────────────────────┐
│  JAR (Java)  com.redrobin.aggressor                            │
│                                                                │
│  RedRobinEventStream  (NEW class, same JAR as RedRobinHttp)    │
│    private final HttpClient client                             │
│    private WebSocket ws (rebuilt on reconnect)                 │
│    private final BlockingQueue<VerdictEvent> queue (size 256)  │
│    private final ScheduledExecutorService reconnectScheduler   │
│                                                                │
│    onText(WebSocket, CharSequence, last):                      │
│      parse JSON via org.json.JSONObject                        │
│      construct VerdictEvent                                    │
│      queue.offer(evt)  // drop oldest semantics                │
│      ws.request(1)                                             │
│                                                                │
│    onError / onClose:                                          │
│      schedule reconnect with exponential backoff (1→2→4→...→60s)│
│                                                                │
│    pollEvent(long timeoutMs): VerdictEvent | null  (BLOCKING)  │
│      Sleep calls this from its fork loop                       │
│                                                                │
│  VerdictEvent  (NEW public-final-fields DTO)                   │
│    public final String type                                    │
│    public final String source         // "layer_a" or "layer_b"│
│    public final String beaconId                                │
│    public final String verdict        // "BLOCK","WARN","ALLOW"│
│    public final String reason                                  │
│    public final String correlationId                           │
│    public final String previousVerdict  // layer_b only        │
└────────────────────────────────────────────────────────────────┘
                          │ Java method calls (no reflection
                          │ on internal classes)
                          ↓
┌────────────────────────────────────────────────────────────────┐
│  ADAPTER (Sleep)  redrobin.cna                                 │
│                                                                │
│  redrobin_init_event_stream  (NEW sub)                         │
│    constructs RedRobinEventStream via existing loader          │
│    $REDROBIN_EVENT_STREAM = new instance                       │
│                                                                │
│  fork(lambda({                                                 │
│      while (1) {                                               │
│        $evt = [$_stream pollEvent: 5000];  # 5s timeout        │
│        if ($evt !is $null && [$evt source] eq "layer_b") {     │
│          blog([$evt beaconId],                                 │
│               "[Red Robin] Layer B " . [$evt verdict] .        │
│               ": " . [$evt reason]);                           │
│        }                                                       │
│      }                                                         │
│  }, $_stream => $REDROBIN_EVENT_STREAM));                      │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 Why this design

| Decision | Justification | Anchor in existing code |
|---|---|---|
| Adapter as another EventBus subscriber | TUI already does this via WS. Single delivery mechanism. | `event_bus.py:9`, `tui_ws.py:26-43` |
| Dedicated `/v1/adapters/events` endpoint | Decouple adapter contract from TUI protocol evolution | `adapters_router.py:22` shows `/v1/adapters/*` namespace convention |
| Server-side filter (`type == "opsec.verdict"`) | YAGNI for v0.1; future-proof for v0.2 manifest filtering | New code; ~5 LOC |
| Token via `?token=<x>` query | WebSocket headers not supported in browser/Java handshake | `tui_ws.py:21` uses same pattern |
| `beacon_id` in Layer B payload | Adapter needs it to route `blog($bid, ...)`. Layer A already includes it — Layer B inconsistency is a pre-existing bug. | `verdict_pipeline.py:232` Layer A includes; `:208-217` Layer B does not |
| JAR with 2 public classes (RedRobinHttp + RedRobinEventStream) | Single JAR ship, separate responsibilities | PyCobalt's json.jar bundles multiple classes |
| `VerdictEvent` as public-final-fields DTO | Sleep accesses via `[$obj fieldName]` — same pattern as `RedRobinHttp.Response` | `RedRobinHttp.java:67-73` precedent (Task #115) |
| `org.json` for JSON parsing | Reliable + same library PyCobalt vendors. JDK lacks native JSON. | Industry standard |
| `lambda()` Sleep fork variable capture | Sleep manual §10. Bypasses fork scope isolation found in Task #110. | `redrobin.cna:352` documents pattern |
| Exponential backoff reconnect | Java 11+ WebSocket API supports it natively via build retry loop | Standard pattern |
| Drop-oldest queue (size 256) | Matches `EventBus` server-side semantics — consistent backpressure model end-to-end | `event_bus.py:11` documents drop-oldest |
| 5-second poll timeout in Sleep fork | Allows fork to wake periodically for shutdown signals + keeps Sleep responsive | Mirror of `ws_client.py:81` `ping_interval=20` cadence (we don't ping; just wake) |

## 5. Components

### 5.1 `redrobin/api/adapter_events_ws.py` (NEW, ~35 LOC)

Mirror of `tui_ws.py` with three differences:
1. Route is `/v1/adapters/events`
2. Skips the `working_memory_snapshot` initial frame (adapter doesn't care about working memory state)
3. Server-side filter: only forwards events with `type == "opsec.verdict"`

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

Register in `app.py` after the existing `include_router(tui_ws_router)`:

```python
from redrobin.api.adapter_events_ws import router as adapter_events_ws_router
# ...
app.include_router(adapter_events_ws_router)
```

### 5.2 `redrobin/application/verdict_pipeline.py` modification

Add `beacon_id` parameter to `_on_retroactive` and include it in the published payload, matching the Layer A payload shape (line 232).

**Current** (`_on_retroactive` around line 195):
```python
async def _on_retroactive(
    self,
    *,
    correlation_id: str,
    original_verdict: str,
    new_verdict: str,
    reason: str,
) -> None:
    if self._bus is None:
        return
    await self._bus.publish(
        {
            "type": "opsec.verdict",
            "payload": {
                "correlation_id": correlation_id,
                "verdict": new_verdict,
                "previous_verdict": original_verdict,
                "reason": reason,
                "source": "layer_b",
            },
        }
    )
```

**New:**
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

Update the caller in `_run_layer_b` (around line 150) to pass `beacon_id` through. `_run_layer_b` already has `beacon_id` in its enclosing scope (the verdict pipeline knows which beacon a verdict belongs to).

Also update the `factory.bind_on_retroactive_verdict` wiring if it exists, so external Layer B implementations also include `beacon_id`.

### 5.3 `protocol/schemas/v0.1/events/opsec_verdict.schema.json` modification

Add `beacon_id` to the schema's `properties`:

```json
{
  "beacon_id": {
    "type": "string",
    "description": "ID of the beacon for which the verdict applies. Required for Layer A; included in Layer B as of v0.1.x."
  }
}
```

Schema validation runs at POST `/v1/events` ingestion. Since the new bus events are produced INTERNALLY (not via POST), the schema doesn't gate them, but documenting it keeps the protocol consistent.

### 5.4 `RedRobinEventStream.java` (NEW, ~150 LOC)

Java translation of `redrobin/tui/ws_client.py`. Same semantics:
- Auto-reconnect loop with exponential backoff
- Per-frame JSON decode (via `org.json.JSONObject`)
- Filter by `type` and `source`
- Bounded queue with drop-oldest

```java
package com.redrobin.aggressor;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.*;
import org.json.JSONObject;

/**
 * WebSocket subscriber that streams opsec.verdict events from the daemon
 * into a bounded queue Sleep can poll.
 *
 * Mirrors redrobin/tui/ws_client.py semantics: auto-reconnect with
 * exponential backoff, per-frame JSON decode, drop-oldest backpressure.
 *
 * The Sleep .cna polls this via pollEvent(timeoutMs). All connection
 * lifecycle, JSON parsing, and reconnect logic lives in this Java class
 * to keep Sleep code minimal and resilient to the fork-scope quirks
 * documented in Task #110.
 */
public class RedRobinEventStream {

    private static final int QUEUE_CAPACITY = 256;
    private static final long BACKOFF_INITIAL_MS = 1_000L;
    private static final long BACKOFF_MAX_MS = 60_000L;

    private final String wsUrl;        // e.g. "ws://127.0.0.1:7777/v1/adapters/events?token=..."
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
        // http(s):// → ws(s)://
        String wsBase = httpBaseUrl.replaceFirst("^http", "ws");
        this.wsUrl = wsBase + "/v1/adapters/events?token=" + bearerToken;
        this.client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        connect();
    }

    public VerdictEvent pollEvent(long timeoutMs) throws InterruptedException {
        return queue.poll(timeoutMs, TimeUnit.MILLISECONDS);
    }

    public void close() {
        shutdown = true;
        if (ws != null) {
            ws.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown").join();
        }
        scheduler.shutdownNow();
    }

    private void connect() {
        if (shutdown) return;
        try {
            this.ws = client.newWebSocketBuilder()
                    .buildAsync(URI.create(wsUrl), new Listener())
                    .get(5, TimeUnit.SECONDS);
            currentBackoffMs = BACKOFF_INITIAL_MS;  // reset on successful connect
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
                // log gap warn — events during this window were lost
                System.err.println("[RedRobinEventStream] reconnected after " + gapMs + "ms — verdicts during gap may have been missed; check episodic.db");
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
                try {
                    VerdictEvent evt = parseVerdictEvent(frame);
                    if (evt != null) {
                        if (!queue.offer(evt)) {
                            queue.poll();        // drop oldest
                            queue.offer(evt);
                        }
                    }
                } catch (Exception ignored) {
                    // malformed frame — log + continue (don't disconnect)
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

    static VerdictEvent parseVerdictEvent(String frame) {
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
    }
}
```

### 5.5 `VerdictEvent.java` (NEW, ~25 LOC)

DTO with public-final-fields so Sleep accesses them via `[$obj fieldName]` (same pattern as `RedRobinHttp.Response` from Task #115). No methods, no equality semantics needed.

```java
package com.redrobin.aggressor;

public class VerdictEvent {
    public final String type;
    public final String source;          // "layer_a" or "layer_b"
    public final String beaconId;
    public final String verdict;         // "BLOCK", "WARN", "ALLOW", "ESCALATE"
    public final String reason;
    public final String correlationId;
    public final String previousVerdict; // populated for layer_b only

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

### 5.6 `RedRobinEventStreamTest.java` (NEW, ~150 LOC)

JUnit 5 tests with an in-process Jetty or Undertow WS server (lightweight, used only for tests). Standard pattern in the JDK ecosystem.

Test cases:
1. `onConnect_subscribesAndReceivesFrames` — sends a verdict frame, asserts pollEvent returns it
2. `parseVerdictEvent_layerB` — unit test of the parser with a realistic Layer B payload
3. `parseVerdictEvent_ignoresNonVerdictTypes` — pass a `host.observation` frame, expect null
4. `pollEvent_blocksUntilTimeout` — empty queue, poll returns null after timeoutMs
5. `queue_dropsOldestWhenFull` — fill queue + 1, oldest is dropped
6. `reconnect_afterServerClose` — kill server, restart, assert client reconnects within backoff window

### 5.7 `helper-jar/lib/json-20240303.jar` (vendored)

Same library PyCobalt uses. ~80 KB. Provenance: `https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar`. SHA-256 recorded in `lib/README.md` (extend the existing one from Task #115).

`build.ps1` and `build.sh` are updated to add `lib/json-20240303.jar` to both `javac -cp` and `jar` packaging. The resulting JAR includes both `redrobin-aggressor-http-1.0.1.jar` classes AND `org.json.*` classes (shaded into one JAR for single-load simplicity).

Actually — looking at PyCobalt more carefully, they ship `jars/json.jar` separately and load via URLClassLoader's URL array. Adopting that pattern:

- `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar` (our code only)
- `adapters/cobaltstrike/jars/json-20240303.jar` (vendored org.json)
- `.cna` URLClassLoader receives BOTH URLs in its array

Cleaner, matches PyCobalt verbatim.

### 5.8 `redrobin.cna` modifications

**Add new init sub** (after existing `redrobin_init_http`):

```sleep
# ===== Event stream init =====
# Load com.redrobin.aggressor.RedRobinEventStream from the same JAR as
# RedRobinHttp + sibling json-20240303.jar (org.json parser). The stream's
# WebSocket connection lives in a Java background thread — Sleep just polls.
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

**Update top-level globals** (`redrobin.cna:23-27`):

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID $REDROBIN_HTTP $REDROBIN_EVENT_STREAM');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
$REDROBIN_HTTP = $null;
$REDROBIN_EVENT_STREAM = $null;
```

**Update boot block** (currently around line 295):

```sleep
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_init_http();
redrobin_init_event_stream();
redrobin_handshake_with_retry();
```

Order: token first (both inits need it), then HTTP, then event stream, then handshake.

**Add Layer B handler fork** (at script tail, after the disabled heartbeat fork comment):

```sleep
# ===== Layer B verdict consumer fork =====
# Async LLM judge (Layer B) finishes 10-30s after the operator typed a
# command and publishes opsec.verdict events. This fork polls the
# RedRobinEventStream's queue and surfaces verdicts to the relevant beacon
# console via blog($bid, ...).
#
# Sleep fork() creates an isolated execution context that does NOT see
# parent globals (verified empirically 2026-05-18). lambda() with explicit
# variable capture is the documented Sleep idiom for this — see Sleep
# manual §10.
fork(lambda({
    local('$evt $msg');
    while (1) {
        $evt = [$_stream pollEvent: 5000];   # 5s timeout, then loop
        if ($evt !is $null) {
            if ([$evt source] eq "layer_b") {
                $msg = "[Red Robin] Layer B " . [$evt verdict] . ": " . [$evt reason];
                blog([$evt beaconId], $msg);
            }
        }
    }
}, $_stream => $REDROBIN_EVENT_STREAM));
```

### 5.9 Smoke test additions (`adapters/cobaltstrike/tests/test_cna_smoke.md`)

Append new section "Layer B verdict visibility smoke (added 2026-05-20)":

```markdown
### TC: Layer B verdict surfaces to beacon console

1. Stock CS Client launch.
2. Load `redrobin.cna`.
3. Type `mimikatz` in a beacon console.
4. Expected within ~1s: `[Red Robin] novel pattern — Layer B reviewing async`.
5. Expected within ~30s: a second line of the form
   `[Red Robin] Layer B BLOCK: Running the built-in mimikatz command...`
   appears in the SAME beacon console.
6. Verify daemon log shows GET `/v1/adapters/events` WebSocket upgrade (and stays connected — no flapping).
7. Cross-check: `python C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py`
   should show the same verdict + reason that appeared in beacon.

### TC: Reconnect after daemon restart

1. With .cna loaded and idle, restart the daemon: stop + start.
2. Within ~30s, JAR should reconnect to the new daemon instance (look for log line in Script Console).
3. Trigger a Layer B verdict (type `mimikatz` in beacon).
4. Verify the verdict appears in the beacon console — proves the new WS connection works.
```

## 6. Behavior contracts

| Contract | Pre-spec | Post-spec |
|---|---|---|
| Layer A sync verdict path | `.cna` calls `/v1/verdict/quick`, gets immediate response, calls `blog`/`berror` | UNCHANGED |
| Layer B async verdict path | Daemon generates, persists to `episodic.db`. Operator never sees it. | Daemon generates, persists, **AND** pushes to subscribed adapters via WS. Operator sees it in beacon. |
| EventBus subscriber semantics | Multi-subscriber, drop-oldest | UNCHANGED |
| Existing TUI behavior | Connects to `/v1/tui`, receives all events | UNCHANGED (separate endpoint, same EventBus internally) |
| Beacon `bid` correlation for verdicts | Layer A includes `beacon_id`; Layer B does NOT | Both include `beacon_id` (bug fixed) |
| Token sanitization in error messages | `redrobin_sanitize_error` strips bearer | UNCHANGED + extends to event-stream errors |
| Operator JVM flags required | None (Task #115) | None — UNCHANGED |

## 7. Java compatibility

- **Floor:** Java 11 (required by `java.net.http.HttpClient.newWebSocketBuilder()`)
- **Tested:** Java 17 (CS 4.12 bundled JVM, per Fortra)
- **Build target:** `--release 11`
- **Future-proof:** WebSocket API is stable since Java 11 — no module access concerns (it's in the exported `java.net.http` package; we don't reflect on internals)

## 8. Security considerations

| Area | Control |
|---|---|
| Bearer token in WS URL | Query parameter (same as TUI). URL not logged. |
| Token in error messages | `redrobin_sanitize_error` already handles HTTP errors. JAR's `onError` logs to `System.err` — must NOT include the URL with token. Verified in code: log lines use class name + Throwable's getMessage(), not the URL. |
| WS frame size cap | Java `HttpClient.WebSocket` enforces ~64KB per text fragment by default. Our verdicts are <2KB. Fine. |
| TLS validation | `ws://localhost:7777` is loopback — no TLS. Production with `wss://` requires JVM's default trust store, which is operator-managed. Out of scope for v0.1. |
| Reconnect storms | Exponential backoff caps at 60s — protects daemon from a flapping adapter. |
| Memory pressure from queue | Bounded at 256 events × ~2KB = ~512KB max. Trivial. |
| Information disclosure via verdicts | Verdicts are operator-facing by design. No new disclosure surface. |

## 9. Test plan

### 9.1 JUnit (in-process, runs on every `build.sh`)

`RedRobinEventStreamTest.java` — see §5.6. Six cases covering connect, parse, filter, poll timeout, drop-oldest, reconnect. ~150 LOC.

### 9.2 Daemon pytest

New file `tests/integration/test_adapter_events_ws.py`:
- WS connect with valid token → accepts
- WS connect with invalid token → closes 1008
- Publish `opsec.verdict` on bus → subscriber receives via WS
- Publish other event type → subscriber does NOT receive (server-side filter)
- Multiple subscribers receive concurrently (verifies EventBus fan-out preserved)

### 9.3 Manual operator smoke (the keystone)

§5.9 above. Two sub-TCs: verdict visibility + reconnect resilience.

## 10. Rollback plan

Each task is a single commit. If any task introduces a regression, `git revert <task-commit>`.

If the entire feature needs to roll back: revert the spec commit and all child commits. The daemon's old behavior (Layer A only visible to operator, Layer B in DB only) is the rollback target — same as v0.1 baseline.

The vendored `json-20240303.jar` is harmless if the consuming code is reverted (just an unused JAR on disk).

## 11. Implementation order

| # | Task | Files | Type |
|---|---|---|---|
| T0 | Reset working tree to HEAD | none | Cleanup |
| T1 | Daemon: add `beacon_id` to Layer B payload + schema | `verdict_pipeline.py`, `opsec_verdict.schema.json`, tests | Bug fix |
| T2 | Daemon: new WS endpoint + register router | `adapter_events_ws.py` (new), `app.py` | Feature |
| T3 | Daemon: pytest for new WS endpoint | `tests/integration/test_adapter_events_ws.py` | Test |
| T4 | Vendor `json-20240303.jar` in helper-jar/lib | `helper-jar/lib/`, `helper-jar/lib/README.md` | Dep |
| T5 | JUnit `RedRobinEventStreamTest.java` (TDD red) | `helper-jar/test/...` | Test |
| T6 | Implement `VerdictEvent` + `RedRobinEventStream` (TDD green) | `helper-jar/src/...` | Feature (KEYSTONE) |
| T7 | Build scripts: include `lib/json-*.jar` in compile+test classpaths; bump JAR version to 1.0.1 | `build.ps1`, `build.sh` | Build |
| T8 | Build new JAR + commit it | `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar`, `adapters/cobaltstrike/jars/json-20240303.jar` | Artifact |
| T9 | `.cna`: add `$REDROBIN_EVENT_STREAM` global + `redrobin_init_event_stream` sub + boot wiring + fork(lambda) | `redrobin.cna` | Feature |
| T10 | Append smoke section to `test_cna_smoke.md` + update `operator-setup.md` | docs | Docs |
| T11 | Comprehensive end-to-end operator smoke (Layer B verdict visible in beacon) | none | Gate |

## 12. Open questions

| # | Question | Tentative resolution |
|---|---|---|
| Q1 | Should the WS frame include the wire envelope (`{spec_version, id, ts, source, event:{...}}`) instead of bus shape (`{type, payload}`)? | **No.** TUI uses bus shape (`tui_ws.py:39`). Adapter mirrors TUI exactly. Wire envelope is for cross-process POST; in-process WS uses bus shape. |
| Q2 | Should Sleep parse JSON itself (no `org.json` dep)? | **No.** JSON parsing in Sleep is fragile (escaped quotes in `reason` field). Pay the ~80KB to vendor `org.json`. Same dep PyCobalt uses. |
| Q3 | Should we shade `org.json` into our JAR or ship two JARs? | **Two JARs** — matches PyCobalt verbatim. Operators see two `.jar` files in `jars/` with clear individual purposes. |
| Q4 | Bump JAR version (1.0.1) or keep 1.0.0? | **1.0.1.** Adding a public class is a minor version. Filename change forces `.cna` to reference the new file by name → no stale jar gotchas. |
| Q5 | Should we also re-enable the heartbeat fork in this spec? | **Yes, as a bonus task.** The `lambda()` capture pattern is established by this work. Re-enabling is ~10 LOC and resolves Task #110 in the same shipment. **Defer to plan** — if scope tightens, split. |
| Q6 | Server-side filtering by manifest `events_consumed`? | **No (YAGNI).** Hardcoded `type == "opsec.verdict"` filter in `adapter_events_ws.py` for v0.1. Generalize when v0.2 adds more consumed types. |
