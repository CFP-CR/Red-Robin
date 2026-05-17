# CNA JAR Helper Design Spec

**Date:** 2026-05-17
**Branch target:** `dev-juan`
**Supersedes:** `docs/superpowers/specs/2026-05-17-cna-http-legacy-revert-design.md` (Option A — invalidated by audit that proved `URL`+`HttpURLConnection` also requires `--add-opens`)
**Also supersedes:** `docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md` (Original HttpClient refactor — requires `--add-opens` for `jdk.internal.net.http`)

## 1. Context

### 1.1 Why this spec exists

Two prior architectures for `adapters/cobaltstrike/redrobin.cna`'s HTTP layer both failed the "stock CS Client launch — no JVM flags" goal:

| Approach | JVM flag required | Audit finding |
|---|---|---|
| **java.net.http.HttpClient** (2026-05-14 spec) | `--add-opens java.net.http/jdk.internal.net.http=ALL-UNNAMED` | Sleep's `getClass().getMethod()` resolves to `jdk.internal.net.http.HttpResponseImpl` (impl class, non-exported package) → JVM access check denied |
| **java.net.URL + HttpURLConnection** (2026-05-17 first spec) | `--add-opens java.base/sun.net.www.protocol.http=ALL-UNNAMED` | Empirical `CheckDispatch.java` on JDK 17/21/22 confirmed 6 of 8 methods (`setRequestMethod`, `setRequestProperty`, `setConnectTimeout`, `setReadTimeout`, `getOutputStream`, `getInputStream`) are `@Override`-d in `sun.net.www.protocol.http.HttpURLConnection` → `Method.getDeclaringClass()` returns `sun.*` (non-exported) → JVM access check denied |

Both approaches share the same root cause: **Sleep reflects on the runtime class of the receiver object, and any method override on a non-exported impl class makes the access check fail.** Adding `--add-opens` is officially "emergency override / technical debt" per [JEP 8305968](https://openjdk.org/jeps/8305968) and is not a production-grade solution.

### 1.2 Why a Java helper JAR is the answer

The JAR pattern (verified production examples: [PyCobalt](https://github.com/dcsync/pycobalt), [DynamicTabRename](https://github.com/EspressoCake/DynamicTabRename)) inverts the problem:

```
Before (broken):              After (this spec):
Sleep --reflects--> JDK       Sleep --reflects--> RedRobinHttp (our class)
       ↓                              ↓
   sun.*/jdk.internal.*           direct invokevirtual to
   (non-exported)                 java.net.http.HttpClient
   → access denied                (no reflection access check)
```

**Why this works without flags:**

1. Sleep's `[$obj method: arg, arg]` dispatches via `Method.invoke` on `obj`'s runtime class.
2. With our JAR, `obj` is an instance of `com.redrobin.aggressor.RedRobinHttp` — a class **we wrote**, in a package **we control**.
3. The JAR is loaded via `URLClassLoader` into the JVM's unnamed module (per [PyCobalt's tested pattern](https://github.com/dcsync/pycobalt/blob/master/aggressor/json.cna)). Sleep's caller class is also in the unnamed module. Both being in the same unnamed module → JVM access check passes trivially.
4. Inside `RedRobinHttp.post(...)`, the bytecode invokes `client.send(...)` via `invokevirtual` — a normal Java method call, not reflection. The link-time access check verified at class-load time that the JAR can access `java.net.http.*` (which IS exported to all modules per the JDK's `module-info.java`).
5. The chain never crosses the unnamed-module → non-exported-internal-module boundary via reflection. No `--add-opens` needed.

### 1.3 Primary-source evidence summary

| Claim | Source |
|---|---|
| Sleep dispatches via `getClass().getMethod()` | Bytecode of `sleep.engine.atoms.ObjectAccess` in `cobaltstrike-client.jar` (verified by Brainstorming Agent #5) |
| `--add-opens` officially "technical debt" | [JEP draft 8305968](https://openjdk.org/jeps/8305968) — "Flags... when used in production, are not a policy but an emergency override of the policy that signifies some technical debt in the code that needs to be resolved." |
| `URL.openConnection` returns `sun.net.www.protocol.http.HttpURLConnection` with overridden methods | OpenJDK 17 source [HttpURLConnection.java](https://github.com/openjdk/jdk17/blob/master/src/java.base/share/classes/sun/net/www/protocol/http/HttpURLConnection.java); empirical `CheckDispatch.java` (Audit Agent #1) |
| `URLClassLoader` is the canonical Sleep JAR loader on Java 9+ | [PyCobalt json.cna](https://github.com/dcsync/pycobalt/blob/master/aggressor/json.cna) (verbatim comment: *"Cobaltstrike's jar loading is broken in jre9+ thanks to some implementation changes by Oracle."*) |
| `script_resource()` returns directory of running .cna | [CS Aggressor `SIGNED_APPLET_RESOURCE` hook docs](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics_aggressor-scripts/as-resources_hooks.htm) — usage: `return script_resource("dist/applet_signed.jar");` |
| Sleep multi-arg method calls use commas | Sleep 2.1 Manual §7.1 (verbatim): *"Specify arguments as a comma separated list separated from the target and message parameters by a colon."* |
| Sleep assignment-loop syntax is `while $var (expr)` not `while ($var = expr)` | Sleep 2.1 Manual §4 Flow Control (verbatim): *"An assignment loop executes a block of code while an expression evaluates to a non-$null value. The assignment loop evaluates expression and assigns to the variable each iteration."* — Syntax: `while variable (expression) { code }` |
| Java HttpClient HTTP/2 default + h2c upgrade | [Oracle Javadoc Java 17 HttpClient](https://docs.oracle.com/en/java/javase/17/docs/api/java.net.http/java/net/http/HttpClient.html); [RFC 7540 §3.2](https://datatracker.ietf.org/doc/html/rfc7540#section-3.2) — handled inside the JAR by setting `HttpClient.Version.HTTP_1_1` explicitly |

## 2. Goals

| ID | Goal |
|---|---|
| **G1** | `.cna` loads and operates on **stock CS Client launch** (`cobaltstrike.exe` / `./cobaltstrike-client` with no JVM flags). |
| **G2** | Handshake, heartbeat, and verdict round-trip all functional end-to-end against the Red Robin daemon. |
| **G3** | All Sleep reflection targets are public classes in packages we control (`com.redrobin.aggressor.*`) — never `jdk.internal.*`, never `sun.*`, never `java.net.http.*` impl. |
| **G4** | All `ismatch` regex calls migrated to `hasmatch` (Sleep's substring operator) per Sleep manual §6. |
| **G5** | All multi-arg method calls use **comma-separated** args per Sleep manual §7.1, NOT colons. |
| **G6** | All read loops use **`while $var (expr)`** Sleep assignment-loop syntax per Sleep manual §4, NOT `while ($var = expr)`. |
| **G7** | Preserve every secondary improvement from prior plans: token sanitization, actionable error logging, handshake retry with exponential backoff, unified heartbeat helper, Java version guard. |
| **G8** | JUnit 5 test suite for the JAR — verified locally against an in-process mock server before any CS Client smoke. |
| **G9** | JAR built reproducibly with stock JDK 11+ tooling (`javac` + `jar`) — no Gradle/Maven required. |
| **G10** | JAR ships pre-built in the repo (sibling to `.cna`) per industry pattern (PyCobalt, DynamicTabRename) — operator does git clone + load .cna with zero build steps. |

## 3. Non-goals

| # | What we are **NOT** doing | Why |
|---|---|---|
| 1 | Async/non-blocking HTTP via `HttpClient.sendAsync` | Synchronous send + Sleep's `fork {}` wrapper is adequate for our load. |
| 2 | HTTP/2 support | Uvicorn doesn't support HTTP/2. JAR forces `HttpClient.Version.HTTP_1_1`. |
| 3 | Connection pool / keep-alive tuning | HttpClient's default pooling is adequate. |
| 4 | Migration of `adapters/cobaltstrike/spike/` scripts | Separate task #114. |
| 5 | Heartbeat fork lifecycle / reload cancellation | Pre-existing issue (Task #110); orthogonal to HTTP refactor. |
| 6 | Building with Gradle or Maven | `javac` + `jar` is simpler and reproducible for a single-class JAR. |
| 7 | CI artifact / GitHub release distribution | Pre-built JAR in git per PyCobalt pattern. Operators clone the repo. |
| 8 | JAR signing / Sigstore | No CS-community precedent requires it (per Audit Agent #4). |
| 9 | Multi-package JAR structure | One package (`com.redrobin.aggressor`), one main class. YAGNI. |
| 10 | JNI / native dependencies | Pure-Java JAR using only `java.net.http`, `java.time`, `java.nio.charset`. |

## 4. Architecture

### 4.1 Repository layout

```
adapters/cobaltstrike/
├── redrobin.cna                              # Sleep script (existing — modified)
├── jars/
│   └── redrobin-aggressor-http-1.0.0.jar    # NEW — pre-built artifact (committed)
├── helper-jar/                              # NEW — Java source root
│   ├── src/
│   │   └── com/redrobin/aggressor/
│   │       └── RedRobinHttp.java
│   ├── test/
│   │   └── com/redrobin/aggressor/
│   │       └── RedRobinHttpTest.java
│   ├── lib/                                 # JUnit JARs (committed for offline build)
│   │   ├── junit-platform-console-standalone-1.10.0.jar
│   │   └── README.md                        # provenance note
│   ├── build.ps1                            # Windows build script
│   ├── build.sh                             # Linux/Mac build script
│   └── README.md                            # build/test instructions
└── tests/
    └── test_cna_smoke.md                    # Manual smoke checklist (existing — modified)

docs/
├── superpowers/specs/
│   └── 2026-05-17-cna-jar-helper-design.md  # this file
├── superpowers/plans/
│   └── 2026-05-17-cna-jar-helper.md         # implementation plan (next step)
└── operator-setup.md                        # existing — modified
```

### 4.2 Runtime layout

```
1. Operator launches CS Client:
   cobaltstrike.exe           ← stock launcher, no JVM flags
                                 (sets -XX:+AggressiveHeap internally per Fortra)

2. Operator: Aggressor Scripts → Load → redrobin.cna

3. redrobin.cna boots:
   - Java version guard (Java 11+ required by HttpClient inside JAR)
   - redrobin_init_http():
       jar_path = script_resource() + "/jars/redrobin-aggressor-http-1.0.0.jar"
       URLClassLoader loads RedRobinHttp.class
       new RedRobinHttp($REDROBIN_URL, $REDROBIN_TOKEN) → $REDROBIN_HTTP
   - redrobin_handshake_with_retry() → uses $REDROBIN_HTTP.post(...)

4. Per HTTP call from Sleep:
   - Sleep:    [$REDROBIN_HTTP post: $path, $body]
               (Method.invoke on RedRobinHttp.post — access check: OK,
                same unnamed module)
   - JAR:      client.send(req, handler)
               (invokevirtual on HttpClient.send — link-time access check
                already passed at class-load: java.net.http IS exported)
   - JVM:      executes HttpClient internals in jdk.internal.net.http
               (this is INTERNAL to the JVM call stack — no reflection
                from outside)
   - JAR returns String body or Response{code,body} to Sleep
```

### 4.3 Wire contract preservation

Same as prior specs: POST/GET path, headers, body byte-identical to daemon contract. Daemon side requires zero changes.

## 5. Components

### 5.1 `helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java`

```java
package com.redrobin.aggressor;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * HTTP helper for the Red Robin Cobalt Strike Aggressor script.
 * <p>
 * Wraps java.net.http.HttpClient so the Sleep-side .cna never reflects on
 * jdk.internal.net.http impl classes. All public methods on this class are
 * Sleep-reflection-safe: declaringClass is com.redrobin.aggressor.RedRobinHttp,
 * which is in the unnamed module (loaded via URLClassLoader) — same module
 * as the Sleep caller, so JVM access check passes trivially.
 */
public class RedRobinHttp {

    private final String baseUrl;
    private final String bearerToken;
    private final HttpClient client;

    /**
     * @param baseUrl daemon URL prefix (e.g. "http://localhost:7777")
     * @param bearerToken transport-token contents (not "Bearer XXX" — just the token)
     */
    public RedRobinHttp(String baseUrl, String bearerToken) {
        this.baseUrl = baseUrl;
        this.bearerToken = bearerToken;
        this.client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofMillis(200))
                .build();
    }

    /**
     * POST JSON body. Throws on any error. Caller is expected to catch.
     */
    public String post(String path, String body) throws Exception {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(Duration.ofMillis(200))
                .header("Authorization", "Bearer " + bearerToken)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        if (resp.statusCode() >= 400) {
            throw new HttpException(resp.statusCode(), resp.body());
        }
        return resp.body();
    }

    /**
     * GET, returning code+body regardless of status. Never throws on HTTP errors;
     * only throws on connect/timeout/DNS failures.
     */
    public Response get(String path) throws Exception {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + path))
                .timeout(Duration.ofMillis(1000))
                .header("Authorization", "Bearer " + bearerToken)
                .GET()
                .build();
        HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
        return new Response(resp.statusCode(), resp.body());
    }

    public static class Response {
        public final int code;
        public final String body;
        public Response(int code, String body) {
            this.code = code;
            this.body = body;
        }
    }

    public static class HttpException extends RuntimeException {
        public final int statusCode;
        public final String responseBody;
        public HttpException(int statusCode, String responseBody) {
            super("HTTP " + statusCode + ": " + responseBody);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }
    }
}
```

**Design notes:**
- `client` is a single instance per `RedRobinHttp` instance (one per .cna load). HttpClient is thread-safe, so the `fork {}` blocks in Sleep can share it.
- `HttpClient.Version.HTTP_1_1` forced — Uvicorn doesn't speak HTTP/2.
- POST throws on `>=400`. Sleep's catch block treats this as "fail-open" → returns empty string → caller falls through to PASS.
- GET returns `Response{code, body}` unconditionally. Heartbeat distinguishes `code==0` (caught exception, never happens here because we re-wrap) from `code!=200`.
- `bearerToken` stored as field, not exposed via getter — never accidentally logged.

### 5.2 `helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java`

```java
package com.redrobin.aggressor;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class RedRobinHttpTest {

    private HttpServer server;
    private String baseUrl;
    private final AtomicReference<String> lastAuth = new AtomicReference<>();
    private final AtomicReference<String> lastBody = new AtomicReference<>();

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/echo-post", ex -> {
            lastAuth.set(ex.getRequestHeaders().getFirst("Authorization"));
            byte[] reqBody = ex.getRequestBody().readAllBytes();
            lastBody.set(new String(reqBody));
            byte[] resp = "{\"adapter_session_id\":\"ses_TEST\"}".getBytes();
            ex.sendResponseHeaders(200, resp.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(resp); }
        });
        server.createContext("/echo-get", ex -> {
            lastAuth.set(ex.getRequestHeaders().getFirst("Authorization"));
            byte[] resp = "{\"adapter\":\"cs-main\",\"healthy\":true}".getBytes();
            ex.sendResponseHeaders(200, resp.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(resp); }
        });
        server.createContext("/error-401", ex -> {
            byte[] resp = "{\"detail\":\"unauthorized\"}".getBytes();
            ex.sendResponseHeaders(401, resp.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(resp); }
        });
        server.start();
        int port = server.getAddress().getPort();
        baseUrl = "http://127.0.0.1:" + port;
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    @Test
    void post_setsBearerAndContentType() throws Exception {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        String result = http.post("/echo-post", "{\"manifest\":{}}");
        assertEquals("Bearer test-token-abc", lastAuth.get());
        assertEquals("{\"manifest\":{}}", lastBody.get());
        assertTrue(result.contains("ses_TEST"));
    }

    @Test
    void get_returnsCodeAndBody() throws Exception {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        RedRobinHttp.Response r = http.get("/echo-get");
        assertEquals(200, r.code);
        assertTrue(r.body.contains("healthy"));
        assertEquals("Bearer test-token-abc", lastAuth.get());
    }

    @Test
    void post_throwsHttpExceptionOn401() {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        RedRobinHttp.HttpException ex = assertThrows(
                RedRobinHttp.HttpException.class,
                () -> http.post("/error-401", "{}")
        );
        assertEquals(401, ex.statusCode);
        assertTrue(ex.responseBody.contains("unauthorized"));
    }

    @Test
    void post_throwsOnConnectionRefused() {
        RedRobinHttp http = new RedRobinHttp("http://127.0.0.1:1", "t");
        assertThrows(Exception.class, () -> http.post("/anything", "{}"));
    }

    @Test
    void get_returnsCodeOnHttp4xx() throws Exception {
        RedRobinHttp http = new RedRobinHttp(baseUrl, "test-token-abc");
        RedRobinHttp.Response r = http.get("/error-401");
        assertEquals(401, r.code);
        assertTrue(r.body.contains("unauthorized"));
    }
}
```

**Why 5 tests:** covers happy-path POST + GET, error semantics (HTTP 4xx → throw on POST, return code on GET), and connect-refused. Adequate coverage for a 150-LOC helper. Uses `com.sun.net.httpserver.HttpServer` (bundled with the JDK, no extra deps).

### 5.3 `helper-jar/build.ps1` (Windows)

```powershell
# Build redrobin-aggressor-http JAR.
# Requires JDK 11+ on PATH.
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VERSION = "1.0.0"
$JAR_NAME = "redrobin-aggressor-http-$VERSION.jar"
$JUNIT = "lib\junit-platform-console-standalone-1.10.0.jar"

# Clean
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path build\classes,build\test-classes,build\jar | Out-Null

# Compile main
Write-Host "Compiling src/..."
javac --release 11 -d build\classes (Get-ChildItem -Recurse src\*.java).FullName

# Compile tests
Write-Host "Compiling test/..."
javac --release 11 -cp "build\classes;$JUNIT" -d build\test-classes (Get-ChildItem -Recurse test\*.java).FullName

# Run tests
Write-Host "Running tests..."
java -jar $JUNIT --class-path "build\classes;build\test-classes" --scan-class-path
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

# Package JAR
Write-Host "Packaging $JAR_NAME..."
jar --create --file "build\jar\$JAR_NAME" -C build\classes .

# Copy to sibling jars/ for the .cna to pick up
$ParentJarsDir = Join-Path (Split-Path -Parent $ScriptDir) "jars"
New-Item -ItemType Directory -Force -Path $ParentJarsDir | Out-Null
Copy-Item "build\jar\$JAR_NAME" -Destination $ParentJarsDir -Force

Write-Host "Built: $ParentJarsDir\$JAR_NAME"
```

### 5.4 `helper-jar/build.sh` (Linux/Mac)

```bash
#!/usr/bin/env bash
# Build redrobin-aggressor-http JAR. Requires JDK 11+ on PATH.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="1.0.0"
JAR_NAME="redrobin-aggressor-http-${VERSION}.jar"
JUNIT="lib/junit-platform-console-standalone-1.10.0.jar"

rm -rf build
mkdir -p build/classes build/test-classes build/jar

echo "Compiling src/..."
find src -name '*.java' -print0 | xargs -0 javac --release 11 -d build/classes

echo "Compiling test/..."
find test -name '*.java' -print0 | xargs -0 javac --release 11 \
    -cp "build/classes:${JUNIT}" -d build/test-classes

echo "Running tests..."
java -jar "${JUNIT}" --class-path "build/classes:build/test-classes" --scan-class-path

echo "Packaging ${JAR_NAME}..."
jar --create --file "build/jar/${JAR_NAME}" -C build/classes .

PARENT_JARS_DIR="$(dirname "$PWD")/jars"
mkdir -p "${PARENT_JARS_DIR}"
cp "build/jar/${JAR_NAME}" "${PARENT_JARS_DIR}/"

echo "Built: ${PARENT_JARS_DIR}/${JAR_NAME}"
```

### 5.5 Sleep — `redrobin_init_http` (new sub)

```sleep
# ===== JAR helper init =====
# Load com.redrobin.aggressor.RedRobinHttp from a sibling jars/ dir via
# URLClassLoader (Sleep's import-from-jar is broken on Java 9+ per PyCobalt
# comment). The .cna never reflects on jdk.internal or sun.* classes —
# Sleep only touches our public RedRobinHttp class, which lives in the
# unnamed module same as the Sleep caller.
sub redrobin_init_http {
    global('$REDROBIN_HTTP');
    local('$jar_path $jar_url $loader $cls $string_cls $ctor');

    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.0.jar';
    $jar_url = [new java.net.URL: 'file://' . $jar_path];
    $loader = [new java.net.URLClassLoader: @($jar_url)];

    $cls = [java.lang.Class forName: 'com.redrobin.aggressor.RedRobinHttp', false, $loader];
    $string_cls = [java.lang.Class forName: 'java.lang.String'];
    $ctor = [$cls getConstructor: @($string_cls, $string_cls)];
    $REDROBIN_HTTP = [$ctor newInstance: @($REDROBIN_URL, $REDROBIN_TOKEN)];
}
```

### 5.6 Sleep — `redrobin_http_post` (rewritten thin)

```sleep
# ===== HTTP POST helper (delegates to JAR; ~150ms target) =====
sub redrobin_http_post {
    local('$path $body $err');
    ($path, $body) = @_;

    try {
        return [$REDROBIN_HTTP post: $path, $body];
    } catch $e {
        $err = redrobin_sanitize_error($e);
        if ($err hasmatch 'ConnectException' || $err hasmatch 'refused') {
            warn("[Red Robin] daemon connection refused at " . $REDROBIN_URL . " — is the daemon running?");
        } else if ($err hasmatch 'HttpTimeout' || $err hasmatch 'timed out') {
            warn("[Red Robin] daemon timeout at " . $REDROBIN_URL . " (>200ms) — slow network or overloaded daemon");
        } else if ($err hasmatch 'UnknownHost') {
            warn("[Red Robin] cannot resolve daemon host in " . $REDROBIN_URL);
        } else if ($err hasmatch 'HttpException') {
            warn("[Red Robin] daemon returned non-2xx for " . $path . " — see daemon log");
        } else {
            warn("[Red Robin] HTTP error: " . $err);
        }
        return "";
    }
}
```

Sleep call sites use **commas** between method args (per Sleep manual §7.1). No reflection on Java internals.

### 5.7 Sleep — `redrobin_http_get` (rewritten thin)

```sleep
# ===== HTTP GET helper =====
# Returns %(code => N, body => "..."). code=0 means caught Java exception
# (connect/timeout/DNS); code>=400 means daemon answered with non-2xx.
sub redrobin_http_get {
    local('$path $result');
    $path = $1;

    try {
        $result = [$REDROBIN_HTTP get: $path];
        return %(code => [$result code], body => [$result body]);
    } catch $e {
        return %(code => 0, body => "");
    }
}
```

`[$result code]` and `[$result body]` access public fields on the inner `Response` class. Sleep handles field access via the same `getMethod` mechanism — fields are in our public class so access check passes.

### 5.8 Sleep — handshake retry (regex fix)

Replace `ismatch` with `hasmatch` per Sleep manual §6 (Audit Concern 1):

```sleep
if ($response hasmatch '"adapter_session_id":"(ses_[^"]+)"') {
    $REDROBIN_SESSION_ID = matched()[0];
    println("[Red Robin] handshake OK; session=" . $REDROBIN_SESSION_ID);
    return;
}
```

### 5.9 Sleep — verdict query (regex fix)

All four verdict regexes (`BLOCK`, `WARN`, `ESCALATE`, plus 2 `reason` capture sites = 5 total) switch from `ismatch` to `hasmatch`. Code identical to Option A spec §5.8, just remove the `.*` workarounds (`hasmatch` is substring by definition).

### 5.10 Sleep — error sanitization (UNCHANGED)

`redrobin_sanitize_error` from T3 of the 2026-05-14 plan is reused verbatim.

### 5.11 Sleep — Java version guard

HttpClient (inside the JAR) requires Java 11+. The version-guard floor stays at **11**. Same code as T8 of the 2026-05-14 plan — no change needed in this refactor.

### 5.12 Sleep — boot block

```sleep
# ===== Boot =====
redrobin_init_http();
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_init_http();             # re-init AFTER token loaded so the JAR-instance has the real token
redrobin_handshake_with_retry();
```

Wait — the JAR ctor takes the token at construction. We must load the token BEFORE constructing the JAR helper. Corrected:

```sleep
# ===== Boot =====
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_init_http();
redrobin_handshake_with_retry();
```

The order is: load token → construct RedRobinHttp(url, token) → handshake.

## 6. Behavior contracts

| Contract | Pre-refactor (Option A revised) | This spec |
|---|---|---|
| Wire shape (POST/GET path/body/headers) | HTTP/1.1 byte-identical | Same — preserved |
| Fail-open on POST error | Returns `""` | Same — Sleep catch converts JAR exceptions to `""` |
| Heartbeat hash shape | `%(code => N, body => "")` | Same — JAR's `Response` mapped 1:1 |
| Token sanitization in error messages | via `redrobin_sanitize_error` | Same — applied in Sleep catch block; JAR doesn't log |
| Handshake retry: 3 attempts with 500/1000/2000ms backoff | Same | Same — preserved |
| Verdict regex match | `hasmatch` (substring) | Same — preserved |
| Java version floor | 11 (current spec relaxed to 8 but reverted because HttpClient is 11+) | 11+ |

## 7. Java compatibility

- **Floor:** Java 11 (HttpClient API minimum).
- **Tested:** Java 17 (CS 4.12 bundled JVM, per Fortra docs).
- **Build target:** `--release 11` (broadest compat; runs on 11, 17, 21, 24, future).
- **Future-proof:** no `jdk.internal.*` or `sun.*` references. No `--add-opens`. JEP 8305968 (Integrity by Default) does not threaten any API used.

## 8. Security considerations

| Area | Control |
|---|---|
| Bearer token storage | Stored as private final field in `RedRobinHttp`. No getter. Not logged. |
| Bearer token in error messages | Sleep catch calls `redrobin_sanitize_error($e)` before warn. The JAR's exception messages do NOT include the Authorization header (only the URL path + status code). |
| Token leakage via JVM heap dumps | Out of scope — same risk as before. If we ever need this, we can `Arrays.fill` a `char[]` token, but Sleep's String token already lives in the JVM heap. |
| JAR integrity | Pre-built JAR committed to git → operator gets the same bytecode as developers. CI optionally computes SHA-256 on every commit. (Not required for v0.1.) |
| Body encoding | `HttpRequest.BodyPublishers.ofString(body)` defaults to UTF-8 (Javadoc) — correct for JSON. |
| Response decoding | `HttpResponse.BodyHandlers.ofString()` defaults to UTF-8 (Javadoc). |
| Response size cap | None. Trusted daemon on `127.0.0.1` — acceptable. |
| `Authorization` header under HTTP/2 upgrade race | Eliminated — JAR forces `HttpClient.Version.HTTP_1_1`. |

## 9. Test plan

### 9.1 JUnit tests (in-process; run during JAR build)

The build script runs `RedRobinHttpTest` (5 tests) before packaging. Failure aborts the build. Coverage:

| Test | Verifies |
|---|---|
| `post_setsBearerAndContentType` | Headers correctly set, body sent verbatim, response returned. |
| `get_returnsCodeAndBody` | GET round-trip with status + body. |
| `post_throwsHttpExceptionOn401` | Non-2xx response → throws (Sleep catch returns "" → fail-open). |
| `post_throwsOnConnectionRefused` | Network error → throws. |
| `get_returnsCodeOnHttp4xx` | GET on 4xx returns code+body (does NOT throw — heartbeat gets diagnostic info). |

### 9.2 Manual smoke (operator-run; the keystone test)

Stock CS Client launch with **no JVM flags**:

```
cobaltstrike.exe              # Windows
./cobaltstrike-client         # Linux/Mac
```

After loading `redrobin.cna`, Script Console must show **none** of:
- `cannot access X in class sun.*`
- `cannot access X in class jdk.internal.*`
- `Unable to make X accessible: module ... does not export ...`

Then the same 7 sub-TCs from the prior smoke section (handshake, heartbeat, verdict round-trip, error logging, token sanitization, retry, Java guard).

A new section "CNA JAR helper smoke (added 2026-05-17)" will be appended to `adapters/cobaltstrike/tests/test_cna_smoke.md` documenting the steps.

## 10. Rollback plan

Each task is a single commit. If any task introduces a regression:

```bash
git revert <task-commit>
```

If the entire JAR pattern fails (extremely unlikely given PyCobalt + DynamicTabRename production track record), restore the current `dev-juan` HEAD by reverting the spec's commit range. The `--add-opens` workaround remains documented as a contingency.

## 11. Implementation order

| # | Task | Files | Type |
|---|---|---|---|
| T0 | Reset working tree to HEAD (discard 2026-05-15/16 experimental edits) | `adapters/cobaltstrike/redrobin.cna` | Cleanup |
| T1 | Create helper-jar directory structure + build scripts | `adapters/cobaltstrike/helper-jar/` | Scaffolding |
| T2 | Write `RedRobinHttp.java` source | `helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` | Java |
| T3 | Write `RedRobinHttpTest.java` (TDD: red→green) | `helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java` | Java tests |
| T4 | Run build script + verify JAR + JUnit pass | `helper-jar/jars/` | Build |
| T5 | Commit pre-built JAR to repo | `adapters/cobaltstrike/jars/` | Distribution |
| T6 | Rewrite `redrobin.cna` HTTP helpers to delegate to JAR | `redrobin.cna` | Sleep (KEYSTONE) |
| T7 | Fix `ismatch` → `hasmatch` regex (handshake + verdict) | `redrobin.cna` | Sleep bugfix |
| T8 | Remove old globals (`$REDROBIN_HTTP_CLIENT`) and init (`redrobin_init_http_client`) | `redrobin.cna` | Cleanup |
| T9 | Remove `java.net.http.*` and `java.time.Duration` imports | `redrobin.cna` | Cleanup |
| T10 | Update `docs/operator-setup.md` (JAR co-location + zero flags) | `docs/operator-setup.md` | Docs |
| T11 | Append "CNA JAR helper smoke" section to `test_cna_smoke.md` | `adapters/cobaltstrike/tests/test_cna_smoke.md` | Docs |
| T12 | Comprehensive end-to-end smoke validation (operator-run gate) | None | Gate |

## 12. Open questions

| # | Question | Tentative resolution |
|---|---|---|
| Q1 | Where do we source JUnit 5 console standalone JAR? | Download `junit-platform-console-standalone-1.10.0.jar` from [Maven Central](https://search.maven.org/artifact/org.junit.platform/junit-platform-console-standalone/1.10.0/jar). Commit to `helper-jar/lib/` for offline-build reproducibility. ~2 MB. |
| Q2 | Do we need a JAR manifest with Main-Class? | No — JAR is a library, not executable. Default manifest is fine. |
| Q3 | Multi-instance .cna loads accumulate `RedRobinHttp` instances + HttpClients | Acknowledged. Each `Unload+Load` cycle creates a new instance; the prior is GC'd when its references go away. The fork-loop accumulation is a separate issue (Task #110). |
| Q4 | What if the JAR is missing at the expected path? | `redrobin_init_http` will throw; Sleep prints error to Script Console. Document this in operator-setup.md troubleshooting. |
| Q5 | Should the JAR ship with a SHA-256 sidecar? | Defer to v0.2. For v0.1 we trust git's content addressing. |
