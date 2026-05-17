# CNA HTTP — Legacy Revert Design Spec

**Date:** 2026-05-17
**Branch target:** `dev-juan`
**Replaces / supersedes:** `docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md`

## 1. Context

### 1.1 Why this spec exists

The 2026-05-14 modernization spec rewrote `adapters/cobaltstrike/redrobin.cna`'s HTTP layer from `java.net.URL` + `HttpURLConnection` (legacy) to `java.net.http.HttpClient` (Java 11+). Goal: eliminate the `--add-opens java.base/sun.net.www.protocol.http=ALL-UNNAMED` JVM flag that operators had to add to the CS Client launcher.

That refactor shipped on `dev-juan` (commits `8bb6642`..`084814d`). Tier 3 smoke testing on 2026-05-15 revealed the refactor's primary goal failed:

- A different module access bug appeared: `class jdk.internal.net.http.HttpResponseImpl ... cannot access a member of class jdk.internal.net.http.HttpResponseImpl (in module java.net.http)` — required adding `--add-opens java.net.http/jdk.internal.net.http=ALL-UNNAMED` instead.
- An HTTP/2 upgrade dance appeared: Java HttpClient's default `Version.HTTP_2` sent `Upgrade: h2c` headers against Uvicorn (HTTP/1.1 only), which Uvicorn rejected with 400. Required forcing `HttpClient.Version.HTTP_1_1`.
- A Sleep regex semantics bug surfaced: `ismatch` is full-string anchored (`Matcher.matches()`), not substring search.
- A Sleep no-arg method dispatch issue: `[$builder GET]` failed silently because the builder's fluent return was discarded by bracket syntax.

The HttpClient refactor swapped one JVM flag (`--add-opens java.base/sun.*`) for another (`--add-opens java.net.http/jdk.internal.*`). The operator still cannot run a stock CS Client launch. Per OpenJDK's own JEP 8305968 (Integrity by Default, draft):

> Flags such as `--add-opens`, `--add-exports`, and `--patch-module`, when used in production, are not a policy but an emergency override of the policy that signifies some technical debt in the code that needs to be resolved.

### 1.2 Root cause (precise)

Sleep dispatches method calls via `obj.getClass().getMethod(name)` (verified in CS 4.12 client JAR bytecode, `sleep.engine.atoms.ObjectAccess`). The returned `Method` object's `declaringClass` is whatever class actually defines the method bytecode. The JVM access check on `Method.invoke()` validates that the caller (Sleep, loaded as the unnamed module) can access that `declaringClass`'s module.

- **`java.net.http.HttpClient` (interface-based API):** `HttpResponse`, `HttpRequest.Builder` are interfaces with abstract methods. The concrete impl classes (`jdk.internal.net.http.HttpResponseImpl`, `HttpRequestBuilderImpl`) must implement every method — so `getMethod()` returns Methods declaring `jdk.internal.net.http.*`. That package is **not exported**. Access check denies.
- **`java.net.URL` + `HttpURLConnection` (abstract-class-based API):** `URLConnection` and `HttpURLConnection` are public abstract classes with **concrete** method implementations (not interfaces). The runtime impl `sun.net.www.protocol.http.HttpURLConnection` inherits most methods without overriding. `getMethod()` returns Methods declaring `java.net.*`. That package **is exported**. Access check passes.

The legacy API was designed before the module system and ships with concrete inherited methods specifically because reflection-friendliness was the JDK 1.x norm. The modern HttpClient API was designed for the module-system era and assumes callers compile against interfaces — which Sleep cannot do.

### 1.3 Primary-source evidence summary

| Claim | Source |
|---|---|
| Sleep `ismatch` = `Matcher.matches()` (full-string anchored), `hasmatch` = `Matcher.find()` (substring) | [Sleep 2.1 Manual §6 — Regular Expressions](http://sleep.dashnine.org/manual/regex.html); confirmed by bytecode in `sleep.bridges.RegexBridge$isMatch` inside `cobaltstrike-client.jar` |
| Sleep method dispatch is via `getClass().getMethod()`; method lookup wins over field lookup | Bytecode in `sleep.engine.atoms.ObjectAccess.evaluate()` |
| Aggressor has no built-in HTTP API; operators use Java stdlib via Sleep brackets | [Fortra Aggressor Functions Reference](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics_aggressor-scripts/as-resources_functions.htm) |
| Industry pattern for HTTP from `.cna` = `URL` + `HttpURLConnection` | [mgeeky's "Safe & sound HTTP request" gist](https://gist.github.com/mgeeky/2d7f8c2a6ffbfd23301e1e2de0312087); [NVISO Labs blog (Mar 2021)](https://blog.nviso.eu/2021/03/05/tap-tap-is-this-thing-on-creating-a-notification-service-for-cobalt-strike/) |
| Java HttpClient defaults to HTTP/2; sends `Upgrade: h2c` over `http://` per RFC 7540 §3.2 | [Java SE 17 `HttpClient` Javadoc](https://docs.oracle.com/en/java/javase/17/docs/api/java.net.http/java/net/http/HttpClient.html); [OpenJDK `HttpRequestImpl.setH2Upgrade`](https://github.com/openjdk/jdk/blob/master/src/java.net.http/share/classes/jdk/internal/net/http/HttpRequestImpl.java); [RFC 7540 §3.2](https://datatracker.ietf.org/doc/html/rfc7540#section-3.2) |
| Uvicorn supports only HTTP/1.1 + WebSocket, rejects h2c with 400 | [Uvicorn `h11_impl.py` `_should_upgrade`](https://github.com/encode/uvicorn/blob/master/uvicorn/protocols/http/h11_impl.py); [Issue #1501](https://github.com/Kludex/uvicorn/issues/1501) |
| `--add-opens` officially classified as "emergency override / technical debt" | [JEP draft 8305968 — Integrity by Default](https://openjdk.org/jeps/8305968) |
| CS 4.12 officially supports OpenJDK 17 (only) | [Cobalt Strike Java dependency docs](https://www.cobaltstrike.com/help-java-dependency) |

## 2. Goals

| ID | Goal |
|---|---|
| **G1** | `.cna` loads and operates on **stock CS Client launch** — zero JVM flags required by the operator. |
| **G2** | Handshake, heartbeat, and verdict round-trip all functional end-to-end against the Red Robin daemon (Uvicorn + FastAPI HTTP/1.1). |
| **G3** | All HTTP code paths reflect only on methods declared in `java.net.*` (exported) — never `jdk.internal.*`, never `sun.*`, never `java.net.http.*`. |
| **G4** | All `ismatch` regex calls migrated to `hasmatch` where substring semantics is intended; cleaner and idiomatic per Sleep manual. |
| **G5** | Preserve every secondary improvement from T1-T10: token sanitization (T3), actionable error logging (T5), handshake retry with exponential backoff (T7), unified heartbeat helper (T6), Java version guard (T8). |
| **G6** | Adapter behavior contract preserved byte-identical at the wire — daemon side requires no changes. |
| **G7** | Pattern matches published industry-standard ([mgeeky](https://gist.github.com/mgeeky/2d7f8c2a6ffbfd23301e1e2de0312087), [NVISO](https://blog.nviso.eu/2021/03/05/tap-tap-is-this-thing-on-creating-a-notification-service-for-cobalt-strike/)) — we are not inventing. |

## 3. Non-goals

| # | What we are **NOT** doing | Why |
|---|---|---|
| 1 | Async/non-blocking HTTP via `HttpClient.sendAsync` | HttpURLConnection is sync; existing `fork {}` wrapper provides async semantics. Adequate for our load. |
| 2 | HTTP/2 support | Uvicorn doesn't speak HTTP/2 per project policy. No benefit. |
| 3 | Java helper JAR (`RedRobinHttp.jar`) | Documented as Option B in spec discussion. Future-proof but adds build pipeline + distribution complexity. Deferred to v0.2 if `URLConnection` pattern ever breaks (no Oracle indication this would happen). |
| 4 | Migration of `adapters/cobaltstrike/spike/` scripts | Separate task (Task #114). |
| 5 | Heartbeat fork lifecycle / reload cancellation | Pre-existing issue (Task #110); orthogonal to HTTP refactor. |
| 6 | Connection pool / keep-alive tuning | HttpURLConnection's default keep-alive is sufficient for our low-volume traffic. |
| 7 | Connection-level disconnect after every request | Adds 5+ LOC per call site for no observable benefit at our QPS. |

## 4. Architecture

### 4.1 Single-file impact

All changes are contained in `adapters/cobaltstrike/redrobin.cna`. Three secondary docs update (spec, operator-setup, smoke test).

### 4.2 Component layout

```
┌─────────────────────────────────────────────────────────┐
│  adapters/cobaltstrike/redrobin.cna                     │
├─────────────────────────────────────────────────────────┤
│  Imports          (§5.1)                                │
│  Globals          (§5.2)                                │
│  Sanitize helper  (§5.3)  — unchanged from T3           │
│  POST helper      (§5.4)  ← REWRITE: HttpURLConnection  │
│  GET helper       (§5.5)  ← REWRITE: HttpURLConnection  │
│  JSON helpers     (§5.6)  — unchanged                   │
│  Handshake retry  (§5.7)  ← fix regex with hasmatch     │
│  Verdict query    (§5.8)  ← fix regex with hasmatch     │
│  Heartbeat        (§5.9)  ← uses new GET helper         │
│  Java version     (§5.10) ← relax floor to Java 8       │
│  Boot block       (§5.11) ← remove HttpClient init      │
└─────────────────────────────────────────────────────────┘
```

### 4.3 Wire contract preservation

POST path/body/headers and GET path are byte-identical to the current state. Daemon side requires zero changes.

| Endpoint | Method | Wire shape |
|---|---|---|
| `/v1/adapters/connect` | POST | Body: existing manifest JSON. Headers: `Authorization: Bearer <token>`, `Content-Type: application/json`. |
| `/v1/verdict/quick` | POST | Body: `{"source_event_id":...,"bid":...,"command":...,"raw":...,"adapter":"cs-main"}`. Same headers. |
| `/v1/events` | POST | Async, fork-wrapped. Same headers. |
| `/v1/adapter/heartbeat?adapter=cs-main` | GET | No body. Bearer auth header. |

## 5. Components

### 5.1 Imports

**Remove** (no longer needed):
```sleep
import java.net.http.*;
import java.time.Duration;
```

**Keep** (already there):
```sleep
import java.net.*;
import java.io.*;
```

`java.net.URL`, `java.net.URLConnection`, `java.net.HttpURLConnection`, `java.net.URI` come from `java.net.*`. Stream readers/writers (`InputStreamReader`, `OutputStreamWriter`, `BufferedReader`) come from `java.io.*`.

### 5.2 Globals

**Remove**: `$REDROBIN_HTTP_CLIENT` (no longer needed — HttpURLConnection isn't a singleton).

**Keep**: `$REDROBIN_URL`, `$REDROBIN_TOKEN`, `$REDROBIN_SESSION_ID`.

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
```

### 5.3 Error sanitization helper — UNCHANGED

`redrobin_sanitize_error` from T3 is reused verbatim. It already takes a Java exception/string and scrubs the bearer token. No changes.

### 5.4 POST helper — REWRITE

```sleep
# ===== HTTP POST helper =====
# Uses java.net.URL + HttpURLConnection. All methods called are declared
# on java.net.URLConnection or java.net.HttpURLConnection (public abstract
# classes in an exported package). Sleep's reflection access check passes
# without any --add-opens JVM flag.
#
# Returns response body string on success, "" on any error (fail-open
# semantics preserved per caller contract).
sub redrobin_http_post {
    local('$path $body $url $con $writer $reader $line $response $err');
    ($path, $body) = @_;

    try {
        $url = [new java.net.URL: $REDROBIN_URL . $path];
        $con = [$url openConnection];
        [$con setRequestMethod: "POST"];
        [$con setRequestProperty: "Authorization": "Bearer " . $REDROBIN_TOKEN];
        [$con setRequestProperty: "Content-Type": "application/json"];
        [$con setConnectTimeout: 200];
        [$con setReadTimeout: 200];
        [$con setDoOutput: true];

        $writer = [new java.io.OutputStreamWriter: [$con getOutputStream], "UTF-8"];
        [$writer write: $body];
        [$writer flush];
        [$writer close];

        $reader = [new java.io.BufferedReader:
                   [new java.io.InputStreamReader: [$con getInputStream], "UTF-8"]];
        $response = "";
        while ($line = [$reader readLine]) {
            $response = $response . $line;
        }
        [$reader close];
        return $response;
    } catch $e {
        $err = redrobin_sanitize_error($e);
        if ($err hasmatch 'ConnectException' || $err hasmatch 'refused') {
            warn("[Red Robin] daemon connection refused at " . $REDROBIN_URL . " — is the daemon running?");
        } else if ($err hasmatch 'SocketTimeout' || $err hasmatch 'timed out') {
            warn("[Red Robin] daemon timeout at " . $REDROBIN_URL . " (>200ms) — slow network or overloaded daemon");
        } else if ($err hasmatch 'UnknownHost') {
            warn("[Red Robin] cannot resolve daemon host in " . $REDROBIN_URL);
        } else if ($err hasmatch 'FileNotFoundException') {
            warn("[Red Robin] daemon returned HTTP 4xx/5xx for " . $path . " — check daemon log");
        } else {
            warn("[Red Robin] HTTP error: " . $err);
        }
        return "";
    }
}
```

**Method declaration audit (all `java.net.*` — exported):**

| Method called | Declared in |
|---|---|
| `URL(String)` constructor | `java.net.URL` |
| `openConnection()` | `java.net.URL` |
| `setRequestMethod(String)` | `java.net.HttpURLConnection` |
| `setRequestProperty(String, String)` | `java.net.URLConnection` |
| `setConnectTimeout(int)` / `setReadTimeout(int)` | `java.net.URLConnection` |
| `setDoOutput(boolean)` | `java.net.URLConnection` |
| `getOutputStream()` | `java.net.URLConnection` |
| `getInputStream()` | `java.net.URLConnection` |
| `OutputStreamWriter(OutputStream, String)` | `java.io.OutputStreamWriter` |
| `BufferedReader(Reader)` | `java.io.BufferedReader` |
| `InputStreamReader(InputStream, String)` | `java.io.InputStreamReader` |
| `write(String)`, `flush()`, `close()`, `readLine()` | `java.io.Writer` / `java.io.BufferedReader` |

Note on 4xx/5xx: `getInputStream()` throws `IOException` (specifically `FileNotFoundException` for 404) when the response is ≥400. The `catch` block handles this. We don't read `getErrorStream()` because the caller already treats empty response as "no verdict / fail-open"; logging the status text is sufficient.

### 5.5 GET helper — REWRITE

```sleep
# ===== HTTP GET helper =====
# Returns %(code => N, body => "..."). code=0 means "no response" (network
# error). code>=400 means daemon answered but rejected.
sub redrobin_http_get {
    local('$path $url $con $reader $line $body $code');
    $body = "";
    $code = 0;

    try {
        $url = [new java.net.URL: $REDROBIN_URL . $path];
        $con = [$url openConnection];
        [$con setRequestMethod: "GET"];
        [$con setRequestProperty: "Authorization": "Bearer " . $REDROBIN_TOKEN];
        [$con setConnectTimeout: 1000];
        [$con setReadTimeout: 1000];

        $code = [$con getResponseCode];
        if ($code >= 200 && $code < 300) {
            $reader = [new java.io.BufferedReader:
                       [new java.io.InputStreamReader: [$con getInputStream], "UTF-8"]];
            while ($line = [$reader readLine]) {
                $body = $body . $line;
            }
            [$reader close];
        }
        return %(code => $code, body => $body);
    } catch $e {
        return %(code => $code, body => "");
    }
}
```

`getResponseCode()` is declared on `java.net.HttpURLConnection` (public abstract) — exported.

### 5.6 JSON helpers — UNCHANGED

`redrobin_escape_json` from T3 is reused verbatim.

### 5.7 Handshake retry — fix regex with `hasmatch`

```sleep
sub redrobin_handshake_with_retry {
    local('$manifest_json $response $attempt $delay');
    $manifest_json = '{"manifest":{"manifest_version":"0.1.0","spec_versions_supported":">=0.1.0,<0.2.0",' .
                     '"name":"cs-aggressor","version":"1.0.0","capabilities":{"preflight_block_console":true,' .
                     '"preflight_block_tui":false,"tui_dispatch":true,"output_observe":true,' .
                     '"console_annotation":true,"emergency_kill_switch":true,"gate_scope":"per_operator",' .
                     '"command_modification":false},"events_emitted":["beacon.command_input","beacon.command_output",' .
                     '"host.observation","credential.observed"],"events_consumed":["opsec.verdict"],' .
                     '"requirements":{"cobalt_strike":">=4.12"}}}';

    $attempt = 1;
    while ($attempt <= 3) {
        $response = redrobin_http_post("/v1/adapters/connect", $manifest_json);
        if ($response hasmatch '"adapter_session_id":"(ses_[^"]+)"') {
            $REDROBIN_SESSION_ID = matched()[0];
            println("[Red Robin] handshake OK; session=" . $REDROBIN_SESSION_ID);
            return;
        }
        $delay = 500 * (1 << ($attempt - 1));
        warn("[Red Robin] handshake attempt " . $attempt . "/3 failed; retrying in " . $delay . "ms");
        sleep($delay);
        $attempt = $attempt + 1;
    }
    warn("[Red Robin] handshake failed after 3 attempts. Verify daemon at " . $REDROBIN_URL . " and reload the .cna.");
}
```

Only delta from T7: `ismatch '"adapter_session_id":...'` → `hasmatch '"adapter_session_id":...'`. Drops the `.*` workaround; uses the idiomatic Sleep substring operator. Sleep 2.1 Manual §6 documents `hasmatch` as the substring-search predicate (`Matcher.find()`).

### 5.8 Verdict query — fix regex with `hasmatch`

```sleep
# Crude JSON parse — extract action field via Sleep hasmatch (substring)
if ($response hasmatch '"action":"BLOCK"') {
    local('$reason');
    if ($response hasmatch '"reason":"([^"]+)"') { $reason = matched()[0]; }
    else { $reason = "see daemon log"; }
    return %(action => "BLOCK", reason => $reason);
} else if ($response hasmatch '"action":"WARN"') {
    local('$reason');
    if ($response hasmatch '"reason":"([^"]+)"') { $reason = matched()[0]; }
    else { $reason = ""; }
    return %(action => "WARN", reason => $reason);
} else if ($response hasmatch '"action":"ESCALATE"') {
    return %(action => "ESCALATE", reason => "Layer B reviewing");
} else {
    return %(action => "PASS", reason => "");
}
```

This was a latent bug: with `ismatch` (full-string anchored) and no `.*` workaround, BLOCK/WARN/ESCALATE never matched and everything silently fell through to PASS. After this fix, real verdict actions reach the operator.

### 5.9 Heartbeat — unchanged from T6

```sleep
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

Functionally same. The fork-loop wrapper at script tail is untouched.

### 5.10 Java version guard — relax to Java 8

The T8 guard checked `>= 11` because HttpClient required Java 11+. With HttpURLConnection, the floor is **Java 8** (HttpURLConnection has been stable since JDK 1.1 but we'll require 8 to match CS's modern launchers).

```sleep
sub redrobin_check_java_version {
    local('$version $major_str $major');
    $version = [System getProperty: "java.version"];

    if ($version is $null) {
        warn("[Red Robin] cannot read java.version system property; assuming Java 8+");
        return true;
    }

    # Java 8 : "1.8.0_392"  → major=1, second=8 → treat as 8
    # Java 9+: "11.0.21" / "17.0.9" → major=11, 17
    $major_str = split('\.', $version)[0];
    $major = parseNumber($major_str);
    if ($major == 1) {
        # Java 8 style version string — second component is the actual major
        $major_str = split('\.', $version)[1];
        $major = parseNumber($major_str);
    }
    if ($major < 8) {
        warn("[Red Robin] FATAL: Java " . $version . " detected. Red Robin requires Java 8+.");
        warn("[Red Robin] Upgrade your CS Client JVM and reload this script.");
        return false;
    }
    return true;
}
```

In practice, CS 4.12 ships Java 17 in its bundled launcher per Fortra docs, so the guard never trips on a stock install. It exists as a safety net for bring-your-own-JVM operators on legacy systems.

**Open question 1:** is the version guard worth keeping? Since CS 4.12 bundles Java 17 and no operator can practically have <8 with modern CS, this is dead code. Resolution at plan-write time: keep it (cheap, defensive).

### 5.11 Boot block — remove HttpClient init

**Remove**: `redrobin_init_http_client()` call from boot. The sub itself can also be removed since no other caller exists.

**Final boot order**:

```sleep
# ===== Boot =====
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake_with_retry();
# (heartbeat fork at bottom of script, unchanged)
```

The Java version guard runs at script-top (already, from T8) and remains there.

## 6. Behavior contracts

| Contract | Pre-refactor (T7+) | Post-refactor (this spec) |
|---|---|---|
| Wire shape (POST path/body/headers, GET path/headers) | HTTP/1.1 byte-identical | Same — preserved |
| Fail-open on POST error | `redrobin_http_post` returns `""` | Same — preserved |
| Heartbeat hash shape | `%(code=>N, body=>"")` | Same — preserved |
| Token sanitization on all error paths | via `redrobin_sanitize_error` | Same — preserved |
| Handshake retry: 3 attempts with 500/1000/2000ms backoff | Same | Same — preserved |
| Verdict regex match | Silently always fell through to PASS (latent bug) | BLOCK/WARN/ESCALATE actually match — new behavior, but matches documented intent |

## 7. Java compatibility

- **Floor:** Java 8. `URL`/`URLConnection`/`HttpURLConnection` were stable in JDK 1.0/1.1.
- **Tested:** Java 17 (CS 4.12 bundled JVM, per Fortra).
- **Future-proof:** No `jdk.internal.*` or `sun.*` references. No `java.net.http.*`. No `--add-opens`. JEP 8305968 (Integrity by Default) does not threaten any API used here.

## 8. Security considerations

| Area | Control |
|---|---|
| Bearer token in error messages | `redrobin_sanitize_error` invoked on every catch path. T3 helper reused. |
| Bearer token in HTTP request | `setRequestProperty("Authorization", "Bearer "+token)` — same wire shape as before. |
| Body encoding | Explicit UTF-8 in `OutputStreamWriter` constructor (default Java charset is platform-dependent — explicit is correct). |
| Response decoding | Explicit UTF-8 in `InputStreamReader` constructor. Same rationale. |
| Response size | `BufferedReader.readLine()` loops until EOF. No size cap. Trusted daemon on `127.0.0.1` — acceptable. If we ever expose this to untrusted origin, add a max-body-size check (out of scope for v0.1). |
| `Authorization` header preservation under HTTP/2 upgrade race | Eliminated — HttpURLConnection is HTTP/1.1 only, no upgrade dance possible. |

## 9. Test plan

### 9.1 Stock-launch verification (the keystone test)

Operator launches CS Client with **no JVM flags whatsoever** — exactly the official Fortra launch path:

```
cobaltstrike.exe                                        # Windows
./cobaltstrike-client                                   # Linux/Mac
```

(`cobaltstrike.exe` internally sets `-XX:+AggressiveHeap` and `-XX:+UseParallelGC` per Fortra's launcher contract. The operator never sees JVM flags.)

After loading the `.cna`, Script Console must show **none** of:
- `cannot access X in class sun.*`
- `cannot access X in class jdk.internal.*`
- `Unable to make ... accessible: module ... does not export ...`

### 9.2 Per-component smoke

All seven sub-steps from `adapters/cobaltstrike/tests/test_cna_smoke.md` "CNA HTTP modernization smoke" section apply, with one modification: TC1 (stock launch) now uses `cobaltstrike.exe` instead of `java --add-opens ... -jar cobaltstrike-client.jar`.

A new sub-section "CNA HTTP legacy-revert smoke" will be appended documenting:

- Stock launch verification (above)
- Handshake OK via `redrobin doctor` + `.cna` load
- Heartbeat round-trip every 30s (daemon log)
- Verdict round-trip with a `mimikatz`-like alias trigger (expect BLOCK or WARN; verifies the `hasmatch` regex actually fires now)
- Error logging on daemon down (`redrobin daemon stop` + alias trigger → "daemon connection refused" warn)
- Token sanitization on a real 401 path (Python one-liner server returning 401 with echoed headers — strengthens TC5 vs the prior weak version)

### 9.3 Cross-version smoke (manual, optional)

If easily available: load on Java 11 and Java 21 (in addition to Fortra's bundled Java 17). Java 8 is theoretical floor but unlikely operator scenario; skip unless an operator reports an issue.

## 10. Rollback plan

Each task is a single commit. If any task introduces a regression:

```bash
git revert <task-commit>
```

In the worst case where the legacy-revert as a whole fails (extremely unlikely given mgeeky/NVISO running this pattern in production), restore the current HttpClient state by reverting the spec's commit range. The current `dev-juan` HEAD (`084814d` plus working-tree edits) is the fallback.

The `--add-opens` workaround stays documented in this spec as a contingency note for emergencies, but operator-setup.md will only document the stock launch.

## 11. Implementation order

The order is chosen so each commit produces a working `.cna` (the keystone HTTP swap happens atomically in one commit; surrounding tasks are pure cleanup/wins).

| # | Task | Files | Risk |
|---|---|---|---|
| T1 | Remove `java.net.http.*` and `java.time.Duration` imports | `redrobin.cna` | None — pure import removal |
| T2 | Remove `$REDROBIN_HTTP_CLIENT` global + `redrobin_init_http_client` sub + boot call | `redrobin.cna` | None — sub becomes unused |
| T3 | Rewrite `redrobin_http_post` with `URL` + `HttpURLConnection` | `redrobin.cna` | **Keystone** — POST helper functional |
| T4 | Rewrite `redrobin_http_get` with `URL` + `HttpURLConnection` | `redrobin.cna` | Keystone — GET helper functional |
| T5 | Migrate handshake regex from `ismatch` `.*X.*` to `hasmatch` `X` | `redrobin.cna` | Low — semantic-preserving cleanup |
| T6 | Migrate verdict regexes from `ismatch` `.*X.*` to `hasmatch` `X` | `redrobin.cna` | Low — same |
| T7 | Relax Java version guard floor 11 → 8 | `redrobin.cna` | None — only affects edge case operators |
| T8 | Update `docs/operator-setup.md`: remove `--add-opens` advice; document stock launch | `docs/operator-setup.md` | None — docs |
| T9 | Append "CNA HTTP legacy-revert smoke" section to `tests/test_cna_smoke.md` | `adapters/cobaltstrike/tests/test_cna_smoke.md` | None — docs |
| T10 | Comprehensive smoke validation (operator-run) | None | Gate task |

## 12. Open questions

| # | Question | Tentative resolution (revisit at plan-write) |
|---|---|---|
| Q1 | Keep Java version guard with floor 8, or remove entirely? | **Keep.** Cheap defense; ~10 LOC; protects future bring-your-own-JVM operators. |
| Q2 | Explicitly `disconnect()` the HttpURLConnection after every call? | **No.** Default keep-alive + GC is fine at our QPS (handshake = 1, heartbeat = 1 every 30s, verdict = a few per minute peak). |
| Q3 | Migrate `adapters/cobaltstrike/spike/` scripts in the same plan? | **No.** Separate task #114. |
| Q4 | Do we tighten POST timeout from 200ms to something larger? | **No.** Current 200ms is documented in spec §5.4. Daemon healthy = ~150ms. Change is out of scope. |
| Q5 | What about a Java helper JAR (Option B)? | **Defer.** Filed as Task #115 / backlog. If `URLConnection` ever stops working (zero indication today), promote B to v0.2. |
