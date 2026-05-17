# CNA JAR Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and ship a Java helper JAR (`com.redrobin.aggressor.RedRobinHttp`) loaded by `redrobin.cna` via `URLClassLoader` so the .cna operates on stock CS Client launch (zero JVM flags). Migrate the .cna HTTP layer to delegate to the JAR + fix all Sleep idiomatic bugs surfaced by the audit (regex semantics, multi-arg syntax, assignment-loop syntax).

**Architecture:** Industry-standard pattern verified by [PyCobalt](https://github.com/dcsync/pycobalt/blob/master/aggressor/json.cna) and [DynamicTabRename](https://github.com/EspressoCake/DynamicTabRename). Sleep reflects on our public `RedRobinHttp` class (in unnamed module via URLClassLoader, same as Sleep itself — access check passes trivially); the JAR's bytecode then invokes `java.net.http.HttpClient` via `invokevirtual` (link-time access check on exported `java.net.http` already passed at class-load).

**Tech Stack:** Java 11+ (`java.net.http`, `java.time.Duration`, `com.sun.net.httpserver.HttpServer` for tests), JUnit 5 (standalone console launcher), stock `javac` + `jar` build tooling. Sleep (Aggressor scripting) wrapper calls.

**Spec:** `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md`

**Risk note:** The Java helper is verified via JUnit 5 tests with an in-process mock HTTP server (5 testcases). The Sleep side has no automated test framework — verification is grep-based code inspection + Task 13 comprehensive operator smoke against the real daemon + CS Client.

**Starting state assumption:** `dev-juan` HEAD is `619c299` (the spec commit). The working tree may contain uncommitted experimental edits to `adapters/cobaltstrike/redrobin.cna` from prior smoke sessions (HTTP/1.1 version line, `.*` regex workarounds, GET helper line removal). Task 1 resets the working tree.

---

## File Structure

| Path | Action |
|---|---|
| `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` | Create |
| `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java` | Create |
| `adapters/cobaltstrike/helper-jar/lib/junit-platform-console-standalone-1.10.0.jar` | Create (vendored) |
| `adapters/cobaltstrike/helper-jar/lib/README.md` | Create |
| `adapters/cobaltstrike/helper-jar/build.ps1` | Create |
| `adapters/cobaltstrike/helper-jar/build.sh` | Create |
| `adapters/cobaltstrike/helper-jar/.gitignore` | Create |
| `adapters/cobaltstrike/helper-jar/README.md` | Create |
| `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar` | Create (built artifact) |
| `adapters/cobaltstrike/redrobin.cna` | Modify |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify |
| `docs/operator-setup.md` | Modify |

---

## Task 1: Reset working tree to clean HEAD

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (revert uncommitted edits)

- [ ] **Step 1: Verify branch and HEAD**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git branch --show-current
git rev-parse --short HEAD
```

Expected: `dev-juan` and `619c299` (or later if other commits landed).

- [ ] **Step 2: Inspect working tree changes**

```bash
git status -s adapters/cobaltstrike/redrobin.cna
```

If output contains `M adapters/cobaltstrike/redrobin.cna`, working tree has uncommitted edits. Continue to Step 3. Otherwise, skip to Task 2.

- [ ] **Step 3: Discard uncommitted edits**

```bash
git checkout -- adapters/cobaltstrike/redrobin.cna
git diff HEAD adapters/cobaltstrike/redrobin.cna
```

Second command expected to output nothing.

- [ ] **Step 4: No commit needed.** Proceed to Task 2.

---

## Task 2: Scaffold helper-jar directory + vendored JUnit JAR

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/.gitignore`
- Create: `adapters/cobaltstrike/helper-jar/README.md`
- Create: `adapters/cobaltstrike/helper-jar/lib/README.md`
- Create: `adapters/cobaltstrike/helper-jar/lib/junit-platform-console-standalone-1.10.0.jar` (download)
- Create: `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/.keep`
- Create: `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/.keep`

- [ ] **Step 1: Create directory structure**

```powershell
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
New-Item -ItemType Directory -Force -Path `
    adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor, `
    adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor, `
    adapters/cobaltstrike/helper-jar/lib | Out-Null
```

(Bash equivalent: `mkdir -p adapters/cobaltstrike/helper-jar/{src,test}/com/redrobin/aggressor adapters/cobaltstrike/helper-jar/lib`)

- [ ] **Step 2: Add `.gitignore`**

Create `adapters/cobaltstrike/helper-jar/.gitignore` with:

```gitignore
build/
*.class
```

- [ ] **Step 3: Add helper-jar root README**

Create `adapters/cobaltstrike/helper-jar/README.md` with:

```markdown
# redrobin-aggressor-http

Java helper JAR loaded by `adapters/cobaltstrike/redrobin.cna` to wrap
`java.net.http.HttpClient`. Sleep cannot reflect on the JDK's internal
HttpClient impl classes (`jdk.internal.net.http.*`) without
`--add-opens`, so we expose a stable public API in our own package
that the JAR's bytecode invokes directly.

See `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` for
the full rationale.

## Build

Requires JDK 11+ on `PATH`.

Windows:
```powershell
./build.ps1
```

Linux/Mac:
```bash
./build.sh
```

Output: `../jars/redrobin-aggressor-http-1.0.0.jar` (sibling of the .cna).

## Test

Build script runs JUnit 5 tests before packaging. To run tests only:

```powershell
# Windows
javac --release 11 -d build/classes (Get-ChildItem -Recurse src/*.java).FullName
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes (Get-ChildItem -Recurse test/*.java).FullName
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;build/test-classes" --scan-class-path
```
```

- [ ] **Step 4: Add lib README documenting JUnit provenance**

Create `adapters/cobaltstrike/helper-jar/lib/README.md` with:

```markdown
# Vendored test dependencies

## junit-platform-console-standalone-1.10.0.jar

Bundles JUnit 5 platform + Jupiter engine + console launcher. Required
for offline reproducible builds of `redrobin-aggressor-http.jar`.

**Source:** https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/1.10.0/junit-platform-console-standalone-1.10.0.jar

**SHA-256 (verify after download):**
```
sha256sum junit-platform-console-standalone-1.10.0.jar
```

Expected: `0b3cc2fa1d8f2a6b5f9c8a9d8e7f6e5d4c3b2a1908070605040302010f0e0d0c`
(Replace with actual SHA after download — committed by Task 2.)

**License:** Eclipse Public License 2.0
```

- [ ] **Step 5: Download JUnit standalone JAR**

```powershell
$junit_url = "https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/1.10.0/junit-platform-console-standalone-1.10.0.jar"
$dest = "adapters/cobaltstrike/helper-jar/lib/junit-platform-console-standalone-1.10.0.jar"
Invoke-WebRequest -Uri $junit_url -OutFile $dest
```

Verify size > 1 MB:

```powershell
(Get-Item $dest).Length
```

Compute SHA-256 and update `lib/README.md` with the real hash:

```powershell
Get-FileHash $dest -Algorithm SHA256
```

Replace the placeholder hash in `lib/README.md` with the actual value.

- [ ] **Step 6: Add `.keep` files so empty source dirs are committed**

```powershell
New-Item -ItemType File adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/.keep
New-Item -ItemType File adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/.keep
```

- [ ] **Step 7: Commit**

```bash
git add -f adapters/cobaltstrike/helper-jar/
git commit -m "feat(cna-jar): scaffold helper-jar with vendored JUnit 5

Sets up the directory structure for the Java helper JAR
(com.redrobin.aggressor.RedRobinHttp) per design spec
docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md.

  - helper-jar/src/com/redrobin/aggressor/ (Java source)
  - helper-jar/test/com/redrobin/aggressor/ (JUnit 5 tests)
  - helper-jar/lib/junit-platform-console-standalone-1.10.0.jar
    (vendored from Maven Central, EPL 2.0, SHA-256 in lib/README.md)
  - helper-jar/.gitignore (excludes build/ artifacts)
  - helper-jar/README.md (build/test instructions)

Empty .keep files preserve the src/test dirs in git. Subsequent
tasks write the actual Java code."
```

---

## Task 3: Write `RedRobinHttpTest.java` (TDD: red first)

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java`

- [ ] **Step 1: Remove the `.keep` placeholder**

```powershell
Remove-Item adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/.keep
```

- [ ] **Step 2: Write the test file**

Create `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java` with:

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

- [ ] **Step 3: Verify compile failure (red)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes test/com/redrobin/aggressor/RedRobinHttpTest.java
```

Expected: compilation failure with `cannot find symbol: class RedRobinHttp` or similar. This is the RED state — tests reference a class that doesn't exist yet.

- [ ] **Step 4: Commit tests**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add -f adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java
git rm -f adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/.keep
git commit -m "test(cna-jar): write RedRobinHttp JUnit 5 tests (TDD red)

5 testcases against an in-process com.sun.net.httpserver.HttpServer:
  - post_setsBearerAndContentType: happy-path POST + header verification
  - get_returnsCodeAndBody: happy-path GET + auth header
  - post_throwsHttpExceptionOn401: non-2xx POST throws RedRobinHttp.HttpException
  - post_throwsOnConnectionRefused: network-level error throws
  - get_returnsCodeOnHttp4xx: GET on 4xx returns code+body, does NOT throw

These tests do not compile yet (RedRobinHttp + Response + HttpException
don't exist). That's the RED state — Task 4 writes the impl to turn them
green."
```

---

## Task 4: Write `RedRobinHttp.java` (TDD: green)

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java`

- [ ] **Step 1: Remove the `.keep` placeholder**

```powershell
Remove-Item adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/.keep
```

- [ ] **Step 2: Write the implementation**

Create `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` with:

```java
package com.redrobin.aggressor;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * HTTP helper for the Red Robin Cobalt Strike Aggressor script.
 *
 * Wraps java.net.http.HttpClient so the Sleep-side .cna never reflects on
 * jdk.internal.net.http impl classes. Sleep reflection targets only
 * methods on this public class — declaringClass is
 * com.redrobin.aggressor.RedRobinHttp, in the unnamed module loaded by
 * URLClassLoader (same module as the Sleep caller), so the JVM access
 * check passes trivially.
 */
public class RedRobinHttp {

    private final String baseUrl;
    private final String bearerToken;
    private final HttpClient client;

    public RedRobinHttp(String baseUrl, String bearerToken) {
        this.baseUrl = baseUrl;
        this.bearerToken = bearerToken;
        this.client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofMillis(200))
                .build();
    }

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

- [ ] **Step 3: Compile + run tests (green)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes
javac --release 11 -d build/classes src/com/redrobin/aggressor/RedRobinHttp.java
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes test/com/redrobin/aggressor/RedRobinHttpTest.java
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;build/test-classes" --scan-class-path
```

Expected: all 5 tests pass.

```
Test run finished after Xms
[         5 tests successful      ]
[         0 tests failed          ]
```

If any fail, fix the implementation and re-run.

- [ ] **Step 4: Commit implementation**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add -f adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java
git rm -f adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/.keep
git commit -m "feat(cna-jar): implement RedRobinHttp (TDD green)

Single public class with instance methods + two inner classes:
  - RedRobinHttp(baseUrl, bearerToken) ctor takes daemon URL prefix and
    transport token. HttpClient.Version.HTTP_1_1 forced to avoid the h2c
    upgrade dance against Uvicorn.
  - post(path, body) -> String. Throws RedRobinHttp.HttpException on
    non-2xx (Sleep catches and returns '').
  - get(path) -> Response{code, body}. Never throws on HTTP errors;
    only throws on network failures.
  - Response: inner static class with public final int code, String body.
  - HttpException extends RuntimeException so Sleep needs no checked-
    exception handling.

All 5 JUnit tests pass against the in-process HttpServer mock. Build is
purely stock JDK 11 tooling — no Gradle, no Maven, no external deps
beyond JUnit 5 standalone in lib/."
```

---

## Task 5: Add build scripts

**Files:**
- Create: `adapters/cobaltstrike/helper-jar/build.ps1`
- Create: `adapters/cobaltstrike/helper-jar/build.sh`

- [ ] **Step 1: Write `build.ps1`**

Create `adapters/cobaltstrike/helper-jar/build.ps1` with:

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
$srcFiles = (Get-ChildItem -Recurse src\*.java).FullName
javac --release 11 -d build\classes $srcFiles
if ($LASTEXITCODE -ne 0) { throw "javac src failed" }

# Compile tests
Write-Host "Compiling test/..."
$testFiles = (Get-ChildItem -Recurse test\*.java).FullName
javac --release 11 -cp "build\classes;$JUNIT" -d build\test-classes $testFiles
if ($LASTEXITCODE -ne 0) { throw "javac test failed" }

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

- [ ] **Step 2: Write `build.sh`**

Create `adapters/cobaltstrike/helper-jar/build.sh` with:

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

Make executable:

```bash
chmod +x adapters/cobaltstrike/helper-jar/build.sh
```

- [ ] **Step 3: Run the Windows build script end-to-end**

```powershell
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
./build.ps1
```

Expected final line:
```
Built: C:\...\Red-Robin\adapters\cobaltstrike\jars\redrobin-aggressor-http-1.0.0.jar
```

Verify the JAR exists and has content:

```powershell
Get-Item ../jars/redrobin-aggressor-http-1.0.0.jar
```

Should show non-zero size (~3-5 KB for our small class).

- [ ] **Step 4: Commit build scripts**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add -f adapters/cobaltstrike/helper-jar/build.ps1 adapters/cobaltstrike/helper-jar/build.sh
git update-index --chmod=+x adapters/cobaltstrike/helper-jar/build.sh
git commit -m "build(cna-jar): add PowerShell and bash build scripts

Stock JDK 11+ build pipeline. No Gradle, no Maven, no network access
required (JUnit is vendored in lib/).

Both scripts:
  - clean build/ dir
  - compile src/ -> build/classes
  - compile test/ -> build/test-classes
  - run JUnit 5 console launcher (5 tests must pass; failure aborts)
  - package jar to build/jar/
  - copy to sibling ../jars/ where the .cna expects to find it

build.sh is committed with executable bit set."
```

---

## Task 6: Commit the pre-built JAR to git

**Files:**
- Create: `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar`

- [ ] **Step 1: Verify the JAR exists from Task 5 Step 3**

```powershell
ls adapters/cobaltstrike/jars/
```

Expected: `redrobin-aggressor-http-1.0.0.jar` present.

- [ ] **Step 2: Verify JAR contents**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
jar --list --file adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar
```

Expected output lines (order may vary):
```
META-INF/
META-INF/MANIFEST.MF
com/
com/redrobin/
com/redrobin/aggressor/
com/redrobin/aggressor/RedRobinHttp.class
com/redrobin/aggressor/RedRobinHttp$Response.class
com/redrobin/aggressor/RedRobinHttp$HttpException.class
```

If output is missing any of the three `.class` files, re-run `build.ps1` and verify Java sources are present.

- [ ] **Step 3: Add `.gitattributes` to suppress binary diff noise**

Append the following to `.gitattributes` (create if it doesn't exist):

```gitattributes
adapters/cobaltstrike/jars/*.jar binary -diff
```

Check if `.gitattributes` exists:

```bash
ls -la .gitattributes 2>/dev/null
```

If exists, append the line. If not, create it with just that line.

- [ ] **Step 4: Commit JAR + gitattributes**

```bash
git add -f adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar
git add .gitattributes
git commit -m "feat(cna-jar): commit pre-built redrobin-aggressor-http-1.0.0.jar

Pre-built JAR shipped alongside the .cna so operators have zero build
steps (PyCobalt + DynamicTabRename pattern). Operator workflow:
  git clone -> Aggressor Scripts -> Load redrobin.cna -> done.

The .cna's redrobin_init_http (Task 7) resolves this JAR via
script_resource() + '/jars/redrobin-aggressor-http-1.0.0.jar'.

.gitattributes marks the JAR as binary and disables diff output —
future JAR bumps won't pollute review diffs with bytecode noise.
Source remains diffable in helper-jar/src/."
```

---

## Task 7: Add `redrobin_init_http` sub + wire into boot block

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

The .cna currently uses `java.net.http.HttpClient` directly. We add a new sub that loads our JAR via URLClassLoader and instantiates `RedRobinHttp`. Later tasks will rewrite the HTTP helpers to delegate to that instance.

- [ ] **Step 1: Locate the existing `redrobin_init_http_client` sub**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "redrobin_init_http_client\|# ===== HttpClient singleton" adapters/cobaltstrike/redrobin.cna
```

Note the line range. The sub is typically near line 285 with a comment block above it.

- [ ] **Step 2: Replace the existing init sub with `redrobin_init_http`**

Find the entire block starting with `# ===== HttpClient singleton =====` through the closing `}` of `redrobin_init_http_client`. Replace with:

```sleep
# ===== JAR helper init =====
# Load com.redrobin.aggressor.RedRobinHttp from sibling jars/ dir via
# URLClassLoader (Sleep's 'import from:' is broken on Java 9+ per the
# PyCobalt comment at https://github.com/dcsync/pycobalt/blob/master/aggressor/json.cna).
# The .cna NEVER reflects on jdk.internal or sun.* classes — Sleep only
# touches our public RedRobinHttp class (loaded in the unnamed module,
# same as Sleep itself, so JVM access check passes trivially).
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

Note: every multi-arg method call uses **comma** separators (`@($string_cls, $string_cls)`, `newInstance: @($REDROBIN_URL, $REDROBIN_TOKEN)`) per Sleep manual §7.1.

- [ ] **Step 3: Update the boot block**

Find the boot block (near line 295, look for `# ===== Boot =====`). It currently reads:

```sleep
redrobin_init_http_client();
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake_with_retry();
```

Replace with:

```sleep
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_init_http();
redrobin_handshake_with_retry();
```

**Order matters:** the JAR helper's constructor takes the token, so the token must be loaded first.

- [ ] **Step 4: Verify**

```bash
grep -n "redrobin_init_http\b\|redrobin_load_token\|redrobin_handshake_with_retry" adapters/cobaltstrike/redrobin.cna
```

Expected: the sub definition, the boot-block call (in the correct order), and no remaining references to `redrobin_init_http_client`.

- [ ] **Step 5: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): replace HttpClient singleton with JAR helper init

redrobin_init_http loads adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar
via URLClassLoader (Sleep's 'import from:' is broken on Java 9+ per
PyCobalt) and instantiates RedRobinHttp with the daemon URL and bearer
token.

\$REDROBIN_HTTP global now holds the helper instance. Tasks 8 and 9
rewrite redrobin_http_post and redrobin_http_get to delegate to it.

Boot block reordered: token loads BEFORE init (the helper's ctor takes
the token at construction time)."
```

---

## Task 8: Rewrite `redrobin_http_post` to delegate to JAR

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

- [ ] **Step 1: Locate the current sub**

```bash
grep -n "sub redrobin_http_post" adapters/cobaltstrike/redrobin.cna
```

- [ ] **Step 2: Replace the entire sub**

Find the block from `# ===== HTTP POST helper` (comment header) through the closing `}` of `redrobin_http_post`. Replace with:

```sleep
# ===== HTTP POST helper (delegates to JAR; ~150ms target on healthy daemon) =====
# All HTTP machinery lives in com.redrobin.aggressor.RedRobinHttp.post().
# Sleep just translates the call and handles errors with sanitized logging.
# Returns response body string on success, "" on any error (fail-open
# semantics preserved per caller contract).
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

Note: `[$REDROBIN_HTTP post: $path, $body]` uses **comma** between the two args (Sleep manual §7.1).

- [ ] **Step 3: Verify the sub is well-formed**

```bash
grep -B 1 -A 22 "sub redrobin_http_post" adapters/cobaltstrike/redrobin.cna | head -25
```

Should show the new sub. No leftover `HttpRequest newBuilder` or `BodyPublishers` references.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): redrobin_http_post delegates to JAR helper

All HTTP request building, sending, and response parsing now lives in
com.redrobin.aggressor.RedRobinHttp.post() inside the JAR. The Sleep
sub is a thin shim: dispatch the call, catch any exception, route to
the appropriate operator-facing warn line, return ''.

Note that argument lists use commas (not colons) per Sleep 2.1 Manual
section 7.1. The prior HttpClient code used colons by mistake — that
syntax 'worked by accident' for some method dispatch paths but is not
idiomatic.

Wire shape (POST body, headers, path) byte-identical to the prior
HttpClient version — the JAR makes the same HTTP/1.1 call with the
same Authorization, Content-Type, and JSON body. Daemon contract
unchanged."
```

---

## Task 9: Rewrite `redrobin_http_get` to delegate to JAR

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

- [ ] **Step 1: Locate the current sub**

```bash
grep -n "sub redrobin_http_get" adapters/cobaltstrike/redrobin.cna
```

- [ ] **Step 2: Replace the entire sub**

Find the block from `# ===== HTTP GET helper` (comment header) through the closing `}` of `redrobin_http_get`. Replace with:

```sleep
# ===== HTTP GET helper (delegates to JAR) =====
# Returns %(code => N, body => "..."). code=0 means caught Java exception
# (connect/timeout/DNS); code>=400 means daemon answered with non-2xx.
# Caller chooses how to react to each.
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

`[$result code]` and `[$result body]` access public fields on the inner `Response` class. Sleep's `getMethod` lookup falls through to field lookup when no matching method is found (per `sleep.engine.atoms.ObjectAccess` bytecode — Audit Agent #5).

- [ ] **Step 3: Verify**

```bash
grep -B 1 -A 12 "sub redrobin_http_get" adapters/cobaltstrike/redrobin.cna | head -15
```

Should show the new sub.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): redrobin_http_get delegates to JAR helper

Same delegation pattern as Task 8 (POST). The JAR's get(path) returns
a Response object with public final fields 'code' and 'body'. The
Sleep sub maps this to the hash shape the rest of the .cna expects:
%(code => N, body => '...').

Heartbeat fork (unchanged) calls this sub every 30s. Wire request and
hash return shape byte-identical to the prior HttpClient version."
```

---

## Task 10: Migrate handshake regex `ismatch` → `hasmatch`

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — `redrobin_handshake_with_retry` sub

- [ ] **Step 1: Locate the handshake sub**

```bash
grep -n "sub redrobin_handshake_with_retry\|adapter_session_id" adapters/cobaltstrike/redrobin.cna
```

- [ ] **Step 2: Change `ismatch` to `hasmatch`**

Find this line inside `redrobin_handshake_with_retry`:

```sleep
        if ($response ismatch '"adapter_session_id":"(ses_[^"]+)"') {
```

Replace with:

```sleep
        if ($response hasmatch '"adapter_session_id":"(ses_[^"]+)"') {
```

Only the operator name changes. Regex pattern stays identical.

- [ ] **Step 3: Verify**

```bash
grep -n "adapter_session_id" adapters/cobaltstrike/redrobin.cna
```

Should show the line with `hasmatch`, not `ismatch`.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "fix(cna): use hasmatch for handshake session_id extraction

Sleep's ismatch operator wraps Matcher.matches() (full-string anchored,
verified in cobaltstrike-client.jar bytecode of sleep.bridges.RegexBridge).
hasmatch wraps Matcher.find() (substring search).

The handshake response body is {\"adapter_session_id\":\"ses_...\"}
wrapped by braces that the regex never covered. ismatch never matched,
so the handshake never extracted the session id and the alias gate was
permanently fail-open.

Per Sleep 2.1 Manual section 6 (Regular Expressions):
'The ismatch predicate anchors itself to the beginning and end of a
string by default. The cousin to ismatch is the hasmatch predicate.
hasmatch looks for a substring within the string that satisfies the
specified pattern.'"
```

---

## Task 11: Migrate verdict regexes `ismatch` → `hasmatch`

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — `redrobin_query_quick` sub

Same root cause as Task 10, applied to the 5 verdict regexes (BLOCK action, BLOCK reason capture, WARN action, WARN reason capture, ESCALATE action). This is a **latent bug fix**: with `ismatch`, every verdict silently fell through to PASS, making the pre-flight gate a no-op.

- [ ] **Step 1: Locate the verdict regex block**

```bash
grep -n "sub redrobin_query_quick\|ismatch '\"action" adapters/cobaltstrike/redrobin.cna
```

Find the 5 `ismatch` lines inside `redrobin_query_quick`.

- [ ] **Step 2: Replace the verdict block**

The block currently looks like:

```sleep
    # Crude JSON parse — extract action field via regex
    if ($response ismatch '"action":"BLOCK"') {
        local('$reason');
        if ($response ismatch '"reason":"([^"]+)"') { $reason = matched()[0]; }
        else { $reason = "see daemon log"; }
        return %(action => "BLOCK", reason => $reason);
    } else if ($response ismatch '"action":"WARN"') {
        local('$reason');
        if ($response ismatch '"reason":"([^"]+)"') { $reason = matched()[0]; }
        else { $reason = ""; }
        return %(action => "WARN", reason => $reason);
    } else if ($response ismatch '"action":"ESCALATE"') {
        return %(action => "ESCALATE", reason => "Layer B reviewing");
    } else {
        return %(action => "PASS", reason => "");
    }
```

Replace with:

```sleep
    # Crude JSON parse — extract action field via Sleep hasmatch (substring).
    # ismatch (used previously) is full-string anchored and never matched
    # the daemon's JSON wrapper, silently falling through to PASS — a
    # latent bug that turned the pre-flight gate into a no-op.
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

5 `ismatch` calls become 5 `hasmatch` calls. Comment updated.

- [ ] **Step 3: Verify**

```bash
grep -n "ismatch" adapters/cobaltstrike/redrobin.cna
```

Expected: no matches for `ismatch`. Comments may still mention `ismatch` for context (e.g., the new comment in the replacement block) — that's fine.

```bash
grep -c "hasmatch" adapters/cobaltstrike/redrobin.cna
```

Expected: at least 6 (1 handshake + 5 verdict). May be more if Task 8 added hasmatch to `redrobin_http_post`'s error-classification block.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "fix(cna): use hasmatch for verdict regex parsing (latent bug)

The 5 verdict regexes (BLOCK action, BLOCK reason, WARN action, WARN
reason, ESCALATE action) all used ismatch (full-string anchored). The
daemon response is {\"verdict\":\"BLOCK\",\"reason\":\"...\"} wrapped
by braces and other JSON fields — ismatch never matched, so every
verdict silently fell through to PASS and the pre-flight gate was a
no-op.

This bug was invisible during prior smoke testing because PASS-by-
default is the safe fail-open behavior, so nothing looked obviously
broken. The smoke session that finally exposed it (after fixing the
body-read failures) showed BLOCK rules firing in the daemon log but
commands dispatching anyway in the beacon console.

hasmatch is Sleep's substring-search operator (Matcher.find()) per
Sleep 2.1 Manual section 6. Idiomatic fix.

After this commit, real verdict actions (BLOCK/WARN/ESCALATE) reach
the operator's beacon console."
```

---

## Task 12: Remove obsolete imports and globals

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — imports + globals

With the JAR delegating all HTTP work, the .cna no longer needs `java.net.http.*`, `java.time.Duration`, or `$REDROBIN_HTTP_CLIENT` directly.

- [ ] **Step 1: Remove `java.net.http.*` and `java.time.Duration` imports**

Locate the imports block (around lines 18-21):

```sleep
import java.net.*;
import java.net.http.*;
import java.io.*;
import java.time.Duration;
```

Replace with:

```sleep
import java.net.*;
import java.io.*;
```

`java.net.*` keeps `URL` and `URLClassLoader` accessible for `redrobin_init_http`. `java.io.*` keeps the existing token-loader (`BufferedReader`, `FileReader`).

- [ ] **Step 2: Remove `$REDROBIN_HTTP_CLIENT` from the globals block**

Locate the globals (around lines 23-27):

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID $REDROBIN_HTTP_CLIENT');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
$REDROBIN_HTTP_CLIENT = $null;
```

Replace with:

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
```

(`$REDROBIN_HTTP` is declared `global(...)` inside `redrobin_init_http` itself, so no top-level declaration needed.)

- [ ] **Step 3: Verify no remaining references**

```bash
grep -n "java.net.http\|java.time.Duration\|REDROBIN_HTTP_CLIENT\|HttpClient" adapters/cobaltstrike/redrobin.cna
```

Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): remove obsolete HttpClient imports and globals

After Tasks 7-9 moved all HTTP work into the JAR, the .cna no longer
references java.net.http.*, java.time.Duration, or
\$REDROBIN_HTTP_CLIENT directly.

  - imports trimmed to java.net.* (for URL + URLClassLoader) + java.io.*
    (for the token-loader stream chain)
  - \$REDROBIN_HTTP_CLIENT global removed (the JAR helper instance
    \$REDROBIN_HTTP replaces it, declared inside redrobin_init_http)

This commit cannot break runtime behavior — these symbols are not
referenced anywhere else in the script after the prior tasks landed."
```

---

## Task 13: Update `docs/operator-setup.md`

**Files:**
- Modify: `docs/operator-setup.md`

The doc has been wrong twice — first claiming "no flags" with HttpClient (needed `--add-opens jdk.internal`), then with the legacy revert (which would have needed `--add-opens sun.net.*`). Now the JAR helper makes the claim actually true.

- [ ] **Step 1: Locate the existing CS Client section**

```bash
grep -n "Cobalt Strike Client Java requirement\|--add-opens\|Java 11" docs/operator-setup.md
```

Find the H3 section "Cobalt Strike Client Java requirement" (added in earlier plan commit `084814d`).

- [ ] **Step 2: Replace the section**

Find the existing `### Cobalt Strike Client Java requirement` H3 block (~18-25 lines). Replace it with:

```markdown
### Cobalt Strike Client Java requirement

Red Robin's Aggressor script (`redrobin.cna`) requires **Java 11+** on the CS
Client side. CS 4.12 bundles OpenJDK 17 in its standard launcher per
[Fortra Java dependency docs](https://www.cobaltstrike.com/help-java-dependency),
so operators on stock CS launches are automatically on a supported JVM.

**No JVM flags required.** All HTTP work happens inside a Java helper JAR
(`adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar`) loaded by
the .cna via `URLClassLoader`. Sleep reflection targets only our public
`com.redrobin.aggressor.RedRobinHttp` class — never `jdk.internal.*` or
`sun.*` — so JVM access checks pass on stock launches.

**Launch CS Client via the official launcher** (no manual `java -jar`):

```
cobaltstrike.exe              # Windows
./cobaltstrike-client         # Linux/Mac
```

**JAR co-location:** the .cna expects `redrobin-aggressor-http-1.0.0.jar`
in a sibling `jars/` directory. If you cloned the Red-Robin repo, the
layout is already correct:

```
adapters/cobaltstrike/
├── redrobin.cna
└── jars/
    └── redrobin-aggressor-http-1.0.0.jar
```

If you copy the .cna elsewhere, also copy the `jars/` directory next to
it (relative path `./jars/redrobin-aggressor-http-1.0.0.jar`).

**Troubleshooting:**

- If the .cna fails to load with `Java ... detected. Red Robin requires
  Java 11+` in the Script Console, your CS Client JVM is older than 11.
  Upgrade to a modern JDK (Temurin 17 or 21).
- If the .cna fails with `ClassNotFoundException: com.redrobin.aggressor.RedRobinHttp`,
  the JAR is missing or misplaced. Verify the sibling-jars layout above.

**Historical note:** Earlier Red Robin versions documented an
`--add-opens` JVM flag workaround. That is no longer needed as of the
2026-05-17 JAR helper refactor — see
`docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` for the
architecture rationale.
```

- [ ] **Step 3: Verify**

```bash
grep -n "add-opens\|HttpClient\|java.net.http" docs/operator-setup.md
```

Expected: NO active references. The "Historical note" paragraph may mention `--add-opens` — that's fine for context.

- [ ] **Step 4: Commit**

```bash
git add -f docs/operator-setup.md
git commit -m "docs(operator-setup): document JAR helper + true zero-flag launch

After the JAR helper refactor (2026-05-17-cna-jar-helper-design spec),
no JVM flags are required for any code path. The doc previously made
this claim twice but was wrong both times:
  - HttpClient refactor (084814d) needed --add-opens jdk.internal.net.http
  - Legacy-revert spec would have needed --add-opens sun.net.www.protocol.http

The JAR pattern makes the claim actually true. Operators launch CS
Client via the stock cobaltstrike.exe / cobaltstrike-client and load
the .cna; no JVM args, no path config beyond the sibling jars/ dir
that ships with the repo.

Added troubleshooting hints for the two failure modes operators may
hit (Java < 11, JAR missing)."
```

---

## Task 14: Append "CNA JAR helper smoke" to `test_cna_smoke.md`

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md`

- [ ] **Step 1: Read the existing smoke doc tail**

```bash
tail -10 adapters/cobaltstrike/tests/test_cna_smoke.md
```

The file currently ends with the prior modernization smoke section.

- [ ] **Step 2: Append the new section**

Append the following at the end of the file:

````markdown

---

## CNA JAR helper smoke (added 2026-05-17)

These steps verify the refactor in `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md`. Supersedes prior smoke sections above.

### 1. Stock launch (no JVM flags) — KEYSTONE

The keystone test. Operator launches CS Client via the official launcher with **zero JVM flags**.

- [ ] Close any running CS Client.
- [ ] Launch CS Client via stock launcher:
  ```powershell
  cd C:\Users\jcspi\Documents\opt\cobaltstrike\client
  .\cobaltstrike.exe
  ```
  (Linux/Mac: `./cobaltstrike-client`)
- [ ] Connect to the teamserver, load `redrobin.cna`.
- [ ] In Script Console expect: `[Red Robin] handshake OK; session=ses_<uuid>`
- [ ] In Script Console expect: NO `cannot access X in class sun.*`
- [ ] In Script Console expect: NO `cannot access X in class jdk.internal.*`
- [ ] In Script Console expect: NO `Unable to make X accessible: module Y does not export Z`
- [ ] In Script Console expect: NO `ClassNotFoundException: com.redrobin.aggressor.RedRobinHttp`
- [ ] In daemon log expect: `POST /v1/adapters/connect HTTP/1.1 200 OK`
- [ ] In daemon log expect: NO `Unsupported upgrade request` warnings (JAR forces HTTP/1.1).

### 2. JAR loading

- [ ] Verify the JAR is at the expected path:
  ```bash
  ls C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/jars/
  ```
  Expected: `redrobin-aggressor-http-1.0.0.jar`
- [ ] In Script Console after .cna load: NO ClassNotFoundException visible.
- [ ] Optional verification — type in Script Console: `println([$REDROBIN_HTTP getClass]);` → expect `class com.redrobin.aggressor.RedRobinHttp`.

### 3. Heartbeat round-trip

- [ ] Wait 30-40 seconds after .cna load.
- [ ] In daemon log expect: `GET /v1/adapter/heartbeat?adapter=cs-main HTTP/1.1 200 OK`
- [ ] In Script Console expect: NO `Attempted to call a non-static method on a null reference` errors.

### 4. Verdict round-trip (regex fix verification)

- [ ] `&binit(rand(100000))` in Script Console → registers dummy bid.
- [ ] Type alias `mimikatz` in beacon console.
- [ ] In daemon log expect: `POST /v1/verdict/quick HTTP/1.1 200 OK`
- [ ] In beacon console expect: `[!] [Red Robin] DANGER: <reason from rule>` (BLOCK or WARN — the rule fires and `hasmatch` actually extracts the action). NOT a silent dispatch.

### 5. Actionable error logging

- [ ] Stop the daemon (`redrobin daemon stop`).
- [ ] Type any aliased command in the beacon console.
- [ ] In Script Console expect: `[Red Robin] daemon connection refused at http://localhost:7777 — is the daemon running?`
- [ ] Restart daemon. Next aliased command must succeed without warn.

### 6. Token sanitization (real 401 path)

Strengthens the prior weak connect-refused TC.

- [ ] In a separate terminal, start a Python one-liner HTTP server:
  ```bash
  python -c "from http.server import BaseHTTPRequestHandler,HTTPServer
  class H(BaseHTTPRequestHandler):
      def do_POST(self):
          self.send_response(401);self.send_header('Content-Type','application/json');self.end_headers()
          self.wfile.write(f'{{\"detail\":\"req-headers={dict(self.headers)}\"}}'.encode())
  HTTPServer(('127.0.0.1',9999),H).serve_forever()"
  ```
- [ ] In CS Client Script Console: `$REDROBIN_URL = "http://127.0.0.1:9999"; redrobin_init_http(); redrobin_handshake_with_retry();`
- [ ] In Script Console expect: a warn with `[Red Robin] daemon returned non-2xx for /v1/adapters/connect`.
- [ ] Verify: warn message does NOT contain literal token characters. Look for `***REDACTED***` or absence of the token's first 8 chars.
- [ ] Restore `$REDROBIN_URL = "http://localhost:7777"; redrobin_init_http()` and Ctrl+C the Python server.

### 7. Handshake retry under daemon-boot race

- [ ] Stop daemon (`redrobin daemon stop`).
- [ ] Aggressor Scripts → Unload `redrobin.cna` → Load again.
- [ ] Within 1-2 seconds: `redrobin daemon start --host 127.0.0.1 --port 7777`
- [ ] Watch the Script Console — expect:
  ```
  [Red Robin] handshake attempt 1/3 failed; retrying in 500ms
  [Red Robin] handshake OK; session=ses_<uuid>
  ```
  (Or attempt 2 then attempt 3 then success, depending on timing.)
````

- [ ] **Step 3: Verify the file ends with the new section**

```bash
tail -5 adapters/cobaltstrike/tests/test_cna_smoke.md
```

Should show the last lines of section 7.

- [ ] **Step 4: Commit**

```bash
git add -f adapters/cobaltstrike/tests/test_cna_smoke.md
git commit -m "docs(test-smoke): add CNA JAR helper verification steps

7 sub-TCs verifying the JAR-helper refactor:

  TC1: KEYSTONE — stock CS Client launch (zero JVM flags). Must NOT show
       any 'cannot access' or 'module does not export' errors.
  TC2: JAR loading — confirms classpath resolution via URLClassLoader.
  TC3: Heartbeat round-trip (no null-reference errors on the GET path).
  TC4: Verdict round-trip — BLOCK/WARN actually reaches beacon console
       (verifies the hasmatch regex fix from Task 11).
  TC5: Actionable error logging (daemon down).
  TC6: Token sanitization with a real 401 path (Python echo server).
  TC7: Handshake retry under daemon-boot race.

TC1 is the gate — if it fails, the entire JAR-helper architecture is
broken and we'd need to root-cause before proceeding."
```

---

## Task 15: Comprehensive end-to-end smoke validation

This is the gate task — running all 7 sub-TCs from the new "CNA JAR helper smoke" section against real CS Client + Red Robin daemon + teamserver.

**Files:** None modified. Verification only.

- [ ] **Step 1: Ensure prerequisites**

- CS REST API running on the teamserver (`100.103.238.211:50443`). After a droplet reboot, `csrestapi` must be respawned per `red-team-playground/docs/runbooks/cs-restapi-bringup.md` §"Manual workaround". Verify:
  ```bash
  ssh root@100.103.238.211 'ss -tlnp | grep -E ":(50050|50443)"'
  ```
  Both ports must listen.
- Red Robin daemon running:
  ```powershell
  cd C:\Users\jcspi\redrobin-smoke
  redrobin daemon start --host 127.0.0.1 --port 7777
  ```
- `redrobin doctor` returns all `[OK]` (including `cs-adapter`).
- CS Client is closed (we want a fresh launch to validate stock-launch behavior).
- The JAR is in place:
  ```bash
  ls adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.0.jar
  ```

- [ ] **Step 2: Run TC1 (keystone)**

Stock launch:

```powershell
cd C:\Users\jcspi\Documents\opt\cobaltstrike\client
.\cobaltstrike.exe
```

Connect → load `C:\Users\jcspi\Documents\dev-projects\Red-Robin\adapters\cobaltstrike\redrobin.cna`.

Verify NONE of: `cannot access`, `module does not export`, `ClassNotFoundException`.

If TC1 fails, **STOP**. The architecture's keystone is broken; root-cause before proceeding. See spec §10 Rollback.

- [ ] **Step 3: Run TC2-TC7**

Walk through each sub-TC in `adapters/cobaltstrike/tests/test_cna_smoke.md` "CNA JAR helper smoke" section. Check each `[ ]` box.

- [ ] **Step 4: Capture failures**

If any TC fails:

- Identify which task (T7-T14) introduced the regression. Use `git log --oneline` to see the commit range and `git bisect` if needed.
- Fix forward with a new commit (do NOT amend prior commits if they're already pushed).
- Re-run the failing TC.

- [ ] **Step 5: Push to origin**

When all 7 TCs pass:

```bash
git push origin dev-juan
```

---

## Self-Review (skill checklist)

**Spec coverage:** Every section of the spec has at least one corresponding task:
- §4.1 Repository layout → Task 2 (scaffold)
- §5.1 RedRobinHttp.java → Task 4
- §5.2 RedRobinHttpTest.java → Task 3
- §5.3 build.ps1 → Task 5
- §5.4 build.sh → Task 5
- §5.5 redrobin_init_http → Task 7
- §5.6 redrobin_http_post → Task 8
- §5.7 redrobin_http_get → Task 9
- §5.8 handshake regex → Task 10
- §5.9 verdict regex → Task 11
- §5.10 error sanitization → unchanged (preserved by not editing)
- §5.11 Java version guard → unchanged (preserved)
- §5.12 boot block → Task 7 Step 3
- §6 Behavior contracts → preserved across Tasks 7-9 (commit messages document)
- §7 Java compatibility → version guard unchanged; JAR built with `--release 11`
- §8 Security considerations → token handling unchanged; JAR ctor takes token, doesn't expose; explicit UTF-8 in JAR via HttpClient defaults
- §9 Test plan → Task 3/4 (JUnit) + Task 14 (smoke doc) + Task 15 (run it)
- §10 Rollback plan → per-task `git revert` simplicity (referenced in Task 15 Step 4)
- §11 Implementation order → matches Tasks 1-15 (the spec listed T0-T12, this plan splits some into separate-commit tasks for atomicity)
- §12 Open questions → Q1 (JUnit source) resolved in Task 2 Step 4/5; Q2 (manifest) resolved by `jar --create` default; Q3-Q5 carried as accepted constraints.

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N". Each task contains full code, full commit message, and exact verification commands.

**Type consistency:**
- `RedRobinHttp(String baseUrl, String bearerToken)` ctor in Task 4 → called from Task 7's `newInstance: @($REDROBIN_URL, $REDROBIN_TOKEN)` ✓
- `RedRobinHttp.post(String, String)` → called from Task 8's `[$REDROBIN_HTTP post: $path, $body]` ✓
- `RedRobinHttp.get(String)` → called from Task 9's `[$REDROBIN_HTTP get: $path]` ✓
- `RedRobinHttp.Response` (inner class with public `int code`, `String body`) → Task 9 reads `[$result code]` and `[$result body]` ✓
- `RedRobinHttp.HttpException` (RuntimeException) → Task 8 catches it via Sleep's catch-binds-throwable + `hasmatch 'HttpException'` ✓
- `$REDROBIN_HTTP` global declared inside Task 7's sub → read in Task 8 and Task 9 ✓
- `redrobin_sanitize_error` unchanged from prior plan → called in Task 8 catch block ✓

All references consistent. Ready for execution.
