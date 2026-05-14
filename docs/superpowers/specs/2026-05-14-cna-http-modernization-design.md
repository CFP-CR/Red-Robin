# CNA HTTP Modernization — Design Spec

**Date:** 2026-05-14
**Status:** Approved (pending implementation plan)
**Component:** `adapters/cobaltstrike/redrobin.cna`
**Owner:** Red Robin v0.1.x team
**Related issue:** Java module access errors when operators load `redrobin.cna`
in CS Client running Java 11+. Discovered during Tier 3 testing on
2026-05-14 (Red Robin smoke run on rtp-attacker-infra teamserver).

## 1. Context

`redrobin.cna` is the Aggressor script loaded by every operator in their
Cobalt Strike Client. It's the technical centerpiece of Red Robin's
per-operator gate model — described in the DEF CON RTV paper as *"28 alias
hooks covering the most-abused commands... when the operator types
`mimikatz logonpasswords` in the Beacon console, the alias fires the
daemon, the daemon returns a verdict in <150ms, and the command either
dispatches (with annotation) or is blocked before it ever reaches the
teamserver."*

The script's HTTP layer (the bridge between Sleep code in CS Client and
Red Robin's daemon) is currently implemented with the legacy
`URL.openConnection()` API plus reflection into `sun.net.www.protocol.http.HttpURLConnection`.
On Java 11+ — which is what every modern CS Client launches — the JVM
module system blocks this reflection by default. The result observed in
the field:

```
cannot access setRequestMethod in class sun.net.www.protocol.http.HttpURLConnection:
class sleep.engine.atoms.ObjectAccess$MethodCallRequest cannot access class
sun.net.www.protocol.http.HttpURLConnection (in module java.base) because
module java.base does not export sun.net.www.protocol.http to unnamed module
```

The `.cna` loads anyway (Sleep tolerates exceptions during alias registration),
but **every HTTP call returns an empty response**, the handshake silently fails,
and **every alias gate fails-open** — operators get false confidence that the
gate is protecting them when it isn't.

A workaround exists (`java --add-opens java.base/sun.net.www.protocol.http=ALL-UNNAMED -jar cobaltstrike-client.jar`).
It is not production-grade because:

- Every operator on the team has to know it and apply it consistently.
- Stock CS Client shortcuts/installers do not include the flag.
- The flag set will grow as more JDK packages get sealed in future Java
  releases (Java 17, 21, and 25 have already removed others).
- On a live DEF CON RTV demo, a Java upgrade between when the demo was
  rehearsed and when it's filmed could break the talk in front of an audience.

## 2. Goals

| # | Goal | How we measure |
|---|------|---------------|
| G1 | Operator can launch CS Client stock (no JVM flags) and load `redrobin.cna` without errors | Manual smoke: Aggressor Script Console shows `[Red Robin] handshake OK` and zero `cannot access sun.*` errors |
| G2 | All HTTP traffic to the daemon byte-identical to today | `tcpdump` / daemon log shows same POST body, headers, paths — no protocol regression |
| G3 | When the daemon is unreachable, the operator sees a *specific* actionable error | `[Red Robin] daemon connection refused at <url> — is the daemon running?` (not the generic empty silence) |
| G4 | Bearer token never appears in any Script Console / log output even on exception | Test: trigger an exception with a 4xx mock; verify token not in logged exception message |
| G5 | Handshake survives the daemon booting concurrently with `.cna` load | Test: load `.cna` while daemon starting; verify retry-with-backoff lands on a successful attempt |
| G6 | Java 11+ requirement enforced explicitly | If operator has Java 8, load aborts with a clear instruction message — not a silent partial load |

## 3. Non-goals

- **Async HTTP throughout.** The current `fork{}`-based concurrency stays.
  `HttpClient.sendAsync()` would be cleaner long term but is YAGNI for v0.1.
- **HTTPS to the daemon.** The daemon-side listener is HTTP loopback only by
  design (transport.token is the auth, not TLS). Adding HTTPS support to the
  `.cna` is a separate v0.2 conversation.
- **TLS pinning to the daemon.** Same reason.
- **Re-implementing the Python-side CS adapter** (`adapter.py`). That's a
  different code path that talks to the CS REST API, not the daemon. Out of
  scope here.
- **Refactoring the spike scripts** under `adapters/cobaltstrike/spike/`. They
  are one-off benchmarks, never loaded in production.
- **Adding CI lint for Sleep code.** Identified during design as a gap but
  deferred to a post-DEF CON follow-up — `test_cna_smoke.md` remains the
  validation gate for this change.

## 4. Architecture

```
.cna load
    │
    ├─► 1. redrobin_check_java_version()    [fail-fast if Java < 11]
    │
    ├─► 2. redrobin_init_http_client()      [singleton, connectTimeout=200ms]
    │
    ├─► 3. $REDROBIN_TOKEN = redrobin_load_token()
    │
    ├─► 4. redrobin_handshake_with_retry()  [3 attempts, 500/1000/2000ms backoff]
    │
    └─► 5. .cna loaded; gate active.
    
At runtime:
    redrobin_query_quick()    ─► redrobin_http_post()   ─► HttpClient.send()
    redrobin_push_event_async ─► fork{} ─► redrobin_http_post()  (fire-and-forget)
    redrobin_heartbeat()       ─► redrobin_http_get()    ─► HttpClient.send()
```

The HttpClient instance is created once at script load and reused for every
subsequent call. Sleep's `fork{}` provides concurrency at the call-site level;
HttpClient itself is thread-safe and supports concurrent `send()` calls.

## 5. Components

### 5.1 Java version guard

```sleep
sub redrobin_check_java_version {
    local('$version $major_str $major');
    $version = [System getProperty: "java.version"];
    # Java 11+: "11.0.21" / "17.0.9" / "21.0.2" / "25.0.1"
    # Java 8 : "1.8.0_392"
    $major_str = split('\.', $version)[0];
    $major = parseNumber($major_str);
    if ($major < 11) {
        warn("[Red Robin] FATAL: Java " . $version . " detected. Red Robin requires Java 11+.");
        warn("[Red Robin] Upgrade your CS Client JVM (e.g. install Temurin 21) and reload this script.");
        return false;
    }
    return true;
}

# Top of script (after imports + globals, before any HTTP):
if (!redrobin_check_java_version()) { return; }
```

The check uses `System.getProperty("java.version")` (public API, always
available) and parses only the leading major-version number. Format is
stable across Java 11–25 inclusive (and per the JEP roadmap will remain
stable through Java 30+).

### 5.2 HttpClient singleton

```sleep
import java.net.http.*;
import java.net.http.HttpRequest$BodyPublishers;
import java.net.http.HttpResponse$BodyHandlers;
import java.time.Duration;

global('$REDROBIN_HTTP_CLIENT');
$REDROBIN_HTTP_CLIENT = $null;

sub redrobin_init_http_client {
    local('$builder');
    $builder = [HttpClient newBuilder];
    [$builder connectTimeout: [Duration ofMillis: 200L]];
    $REDROBIN_HTTP_CLIENT = [$builder build];
}
```

A single `HttpClient` is created at script load. It's thread-safe and
internally manages a connection pool — multiple concurrent `send()` calls
from forks share the underlying connections.

**Lifecycle note:** when the operator unloads + reloads the `.cna`, the old
`HttpClient` reference is dropped but not explicitly closed. The JVM will
reap underlying TCP connections via standard GC. Acceptable for normal
engagement patterns (load once per CS Client session); a long-running CS
Client with many unload/reload cycles will accumulate dangling resources
slowly. Mitigation deferred — operators are expected to restart CS Client
periodically anyway.

### 5.3 Error sanitization

```sleep
sub redrobin_sanitize_error {
    local('$err_str');
    $err_str = "" . $1;   # coerce to string
    # Strip the actual bearer token, if it has been loaded
    if (strlen($REDROBIN_TOKEN) > 8) {
        $err_str = replace($err_str, $REDROBIN_TOKEN, '***REDACTED***');
    }
    # Generic catch-all for any "Bearer ..." pattern that may leak
    $err_str = replace($err_str, 'Bearer [A-Za-z0-9_\-\.=]+', 'Bearer ***REDACTED***');
    return $err_str;
}
```

Called on EVERY exception path before logging. The Aggressor Script Console
is frequently visible in engagement screenshots / shared screens — leaking
the bearer token there is a high-impact secret exposure.

### 5.4 POST helper (`redrobin_http_post`)

Returns the raw response body string on success, or `""` on any error
(preserving the existing fail-open contract). Logs an actionable error
message in each failure category.

```sleep
sub redrobin_http_post {
    local('$path $body $reqBuilder $request $response $err');
    ($path, $body) = @_;

    $reqBuilder = [HttpRequest newBuilder];
    [$reqBuilder uri: [URI create: $REDROBIN_URL . $path]];
    [$reqBuilder timeout: [Duration ofMillis: 200L]];
    [$reqBuilder header: "Authorization": "Bearer " . $REDROBIN_TOKEN];
    [$reqBuilder header: "Content-Type": "application/json"];
    [$reqBuilder POST: [BodyPublishers ofString: $body]];
    $request = [$reqBuilder build];

    try {
        $response = [$REDROBIN_HTTP_CLIENT send: $request: [BodyHandlers ofString]];
        return [$response body];
    } catch $e {
        $err = redrobin_sanitize_error($e);
        if ($err ismatch '.*ConnectException.*' || $err ismatch '.*refused.*') {
            warn("[Red Robin] daemon connection refused at " . $REDROBIN_URL . " — is the daemon running?");
        } else if ($err ismatch '.*HttpTimeout.*' || $err ismatch '.*timed out.*') {
            warn("[Red Robin] daemon timeout at " . $REDROBIN_URL . " (>200ms) — slow network or overloaded daemon");
        } else if ($err ismatch '.*UnknownHost.*') {
            warn("[Red Robin] cannot resolve daemon host in " . $REDROBIN_URL);
        } else {
            warn("[Red Robin] HTTP error: " . $err);
        }
        return "";
    }
}
```

### 5.5 GET helper (`redrobin_http_get`)

Returns a `%(code => N, body => "...")` hash. Used by the heartbeat loop;
designed so the caller can distinguish HTTP error codes from network failure
(both useful: `code=0` means "no response", `code>=400` means "daemon answered
but rejected the request").

```sleep
sub redrobin_http_get {
    local('$path $reqBuilder $request $response $err');
    $path = $1;

    $reqBuilder = [HttpRequest newBuilder];
    [$reqBuilder uri: [URI create: $REDROBIN_URL . $path]];
    [$reqBuilder timeout: [Duration ofMillis: 1000L]];
    [$reqBuilder GET];
    $request = [$reqBuilder build];

    try {
        $response = [$REDROBIN_HTTP_CLIENT send: $request: [BodyHandlers ofString]];
        return %(code => [$response statusCode], body => [$response body]);
    } catch $e {
        return %(code => 0, body => "");
    }
}

sub redrobin_heartbeat {
    local('$result $code');
    $result = redrobin_http_get("/v1/adapter/heartbeat?adapter=cs-main");
    $code = $result["code"];
    if ($code == 0) {
        warn("[Red Robin] daemon unreachable; alias gates will fail-open");
    } else if ($code != 200) {
        warn("[Red Robin] daemon heartbeat returned " . $code . "; alias gates may fail-open");
    }
}
```

### 5.6 Handshake with retry

```sleep
sub redrobin_handshake_with_retry {
    local('$manifest_json $response $attempt $delay');
    $manifest_json = '{"manifest":{...}}';   # literal manifest unchanged from current redrobin.cna lines 172-178

    $attempt = 1;
    while ($attempt <= 3) {
        $response = redrobin_http_post("/v1/adapters/connect", $manifest_json);
        if ($response ismatch '"adapter_session_id":"(ses_[^"]+)"') {
            $REDROBIN_SESSION_ID = matched()[0];
            println("[Red Robin] handshake OK; session=" . $REDROBIN_SESSION_ID);
            return;
        }
        $delay = 500 * (1 << ($attempt - 1));   # 500ms, 1000ms, 2000ms
        warn("[Red Robin] handshake attempt " . $attempt . "/3 failed; retrying in " . $delay . "ms");
        sleep($delay);
        $attempt = $attempt + 1;
    }
    warn("[Red Robin] handshake failed after 3 attempts. Verify daemon at " . $REDROBIN_URL . " and reload the .cna.");
}
```

Total budget if all 3 attempts fail: ~3.5 seconds (3×200ms timeout + 500+1000+2000ms backoff). Acceptable — handshake runs once at load, not in the hot path.

## 6. Behavior contracts

### Preserved (zero change vs current)

- **Fail-open semantics.** Any HTTP error → response body is `""`; the calling alias gate treats empty as "no verdict" → command dispatches normally. This is the intentional safety property of the gate (better to let a command through than to block all commands when the daemon is down).
- **Wire shape.** POST body, headers, paths, methods, ports — byte-for-byte identical. Daemon side has zero changes.
- **`fork{}` async push.** `redrobin_push_event_async` and the heartbeat loop continue to use `fork{}` as today. Only the inner HTTP call changes.
- **Token loading.** Continues to load from `${user.home}/.redrobin/transport.token` via the existing `redrobin_load_token` sub. Unchanged.

### New (added by this refactor)

- **Java < 11 → fail-fast.** Old behavior was a silent partial load with broken HTTP. New behavior is an explicit fatal message.
- **Distinct error messages per failure type.** Connection refused, timeout, DNS, generic — each produces a distinct `warn()` line in the Script Console.
- **Token redacted from all error logs.** Old behavior never printed the token, but exception toString in some JVMs could leak it. New behavior guarantees redaction regardless of what the exception object contains.
- **Handshake retries 3× with backoff.** Old behavior was 1 attempt; if daemon was booting concurrent with `.cna` load, handshake failed and the operator had to manually reload.

## 7. Java compatibility

- **Floor: Java 11.** `HttpClient`, `HttpRequest`, `HttpResponse`, and
  `Duration` are all available in JDK 11+.
- **Verified up to:** Java 25.0.1 (the version observed on the deployed
  teamserver during 2026-05-14 testing).
- **CS Client compatibility:** No change. CS 4.7+ bundles Java 11+ and
  ships its own JVM, so operators on stock CS distributions are
  automatically on Java 11+.

The Java 8 incompatibility is intentional. CS 4.10 dropped Java 8 support
in 2023; any operator still on Java 8 has a much older problem than this
refactor.

## 8. Security considerations

| Threat | Mitigation in this design |
|--------|---------------------------|
| Token leakage via exception toString in operator logs / shared screens | `redrobin_sanitize_error` regex-strips both the literal token and any `Bearer ...` pattern |
| Token exposure via `HttpClient` debug logging (some JDKs enable this with `-Djdk.httpclient.HttpClient.log=...`) | Documented as out of scope — operator must not enable JDK HTTP debug logging during engagements; documented in operator-setup.md as a no-no |
| Empty token sent in handshake before `redrobin_load_token()` runs | Boot order guarantees `$REDROBIN_TOKEN` is set BEFORE `redrobin_handshake_with_retry` is called. If load_token fails, handshake will fire with empty Authorization header — daemon returns 401, our error logging surfaces it clearly |

## 9. Test plan

### Manual smoke test (gate to commit)

Tracked in `adapters/cobaltstrike/tests/test_cna_smoke.md` (this spec
includes the verification steps but the actual checklist lives there;
the smoke doc gets updated as part of the implementation).

1. **Java version guard:**
   - On a CS Client with Java 11+, load `.cna` → expect no warn from
     version guard, normal handshake flow.
   - Stub the check to return Java 8 (e.g., monkey-patch `$version`) →
     expect `FATAL: Java 1.8.x detected` and load aborts.

2. **HTTP modernization (G1, G2):**
   - Launch CS Client **stock** (no `--add-opens`).
   - Connect to teamserver, load `redrobin.cna`.
   - In Script Console: NO `cannot access sun.*` errors, NO "non-static
     method on null reference", `[Red Robin] handshake OK; session=ses_<uuid>`
     appears.
   - In daemon log: `POST /v1/adapters/connect 200 OK`.

3. **Verdict dispatch round-trip (G2 wire check):**
   - `&binit(rand(100000))` to register a dummy bid.
   - Fire alias `mimikatz` in the console → expect BLOCK with
     `rule_id: rule-block-mimikatz` (or whatever the sample rule emits),
     `POST /v1/verdict/quick 200 OK` in daemon log.

4. **Heartbeat (G2 GET path):**
   - Wait 30s after .cna load.
   - Expect `GET /v1/adapter/heartbeat?adapter=cs-main 200 OK` in daemon log.

5. **Actionable error logging (G3):**
   - Stop the daemon mid-session.
   - Fire any alias → expect `[Red Robin] daemon connection refused at
     http://localhost:7777 — is the daemon running?` in Script Console.
   - Restart daemon → next alias dispatch succeeds.

6. **Token sanitization (G4):**
   - Configure `$REDROBIN_URL` to point at an endpoint returning HTTP 401
     (any 4xx response will do; can mock with a one-line python http server).
   - Fire any alias.
   - Verify `[Red Robin] HTTP error: ...` line in Script Console contains
     `***REDACTED***` and does NOT contain the actual token value.

7. **Handshake retry (G5):**
   - Stop daemon.
   - Begin loading `.cna` (script will attempt handshake).
   - Within 2 seconds: start the daemon.
   - Expect: first handshake attempt fails with `connection refused`, then
     either attempt 2 (after 500ms) or attempt 3 (after 1500ms total)
     succeeds with `handshake OK`.

8. **Java version guard (G6):** see step 1.

### Diagnostic test artifacts

The test_cna_smoke.md doc gets updated to include curl-equivalent commands
for each verification, so the test is reproducible by a future operator
running through it independently.

## 10. Rollback plan

Single-commit refactor. If post-deploy the new `.cna` misbehaves on a
specific JDK or in a specific CS Client version we hadn't tested:

```bash
git revert <commit>
```

The reverted code with the `--add-opens` workaround was the documented
state of the system before this commit, so operators can fall back to that
mode with no other changes needed.

No DB migrations, no daemon-side changes, no broken contracts elsewhere.

## 11. Implementation order (sketch — proper plan in writing-plans)

1. Add imports (`java.net.http.*`, nested static classes, `java.time.Duration`).
2. Add `$REDROBIN_HTTP_CLIENT` global + `redrobin_init_http_client` sub.
3. Add `redrobin_check_java_version` + gate at top of script.
4. Add `redrobin_sanitize_error`.
5. Rewrite `redrobin_http_post` (Sleep style preserved).
6. Add `redrobin_http_get` (new helper, replaces inline GET in heartbeat).
7. Rewrite `redrobin_heartbeat` to call `redrobin_http_get`.
8. Replace `redrobin_handshake` with `redrobin_handshake_with_retry`.
9. Update boot block (line ~195) to call init + retry-handshake.
10. Update `test_cna_smoke.md` with new verification steps.
11. Run the manual smoke test end-to-end.

## 12. Open questions

| # | Question | Resolution plan |
|---|----------|-----------------|
| Q1 | Does Sleep correctly resolve `HttpRequest$BodyPublishers` as a nested static class via the import? | Test during implementation. If `Unknown class` error appears, fallback to fully-qualified `[java.net.http.HttpRequest$BodyPublishers ofString: $body]` inline. |
| Q2 | Will `[Duration ofMillis: 200L]` accept the `200L` Sleep literal as a `long` parameter, or do we need explicit boxing? | Test during implementation. Fallback: `[Long parseLong: "200"]` (wrapper class — capital L) or `cast(200, ^long)` if necessary. |
| Q3 | Does Sleep's `try/catch` correctly catch `java.net.http.HttpTimeoutException` and `java.net.ConnectException`, which are checked exceptions in some method signatures? | Test during implementation. Sleep should reflect properly because it's all runtime — checked vs unchecked is a compile-time concept. If problems surface, wrap in additional Sleep-level `try` around the `send()` call. |
| Q4 | Are there any other Sleep scripts in the project that share this HTTP pattern and should be refactored at the same time? | `spike/test_http_latency.cna` does — but it's a one-off benchmark, not production code. Explicitly excluded from this refactor's scope (non-goal #5). Track as a separate v0.1.x cleanup task. |
