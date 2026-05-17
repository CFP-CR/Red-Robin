# CNA HTTP Legacy-Revert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revert `adapters/cobaltstrike/redrobin.cna`'s HTTP layer from `java.net.http.HttpClient` to `java.net.URL` + `HttpURLConnection` so the .cna loads on stock CS Client launch with zero JVM flags. Migrate all `ismatch` regex calls to `hasmatch` (Sleep's substring operator) and preserve every secondary win from T1-T10 of the 2026-05-14 plan (sanitization, retry, error logging, version guard, unified heartbeat helper).

**Architecture:** Industry-standard pattern verified by [mgeeky's gist](https://gist.github.com/mgeeky/2d7f8c2a6ffbfd23301e1e2de0312087) and [NVISO blog](https://blog.nviso.eu/2021/03/05/tap-tap-is-this-thing-on-creating-a-notification-service-for-cobalt-strike/). All HTTP code paths reflect only on methods declared in `java.net.*` (exported package), so Sleep's reflection access check passes without `--add-opens`. HttpURLConnection is HTTP/1.1 only, which matches our Uvicorn server and eliminates the h2c upgrade dance.

**Tech Stack:** Sleep (Aggressor scripting), `java.net.URL`, `java.net.HttpURLConnection` (JDK 1.1+), `java.io.*` (streams).

**Spec:** `docs/superpowers/specs/2026-05-17-cna-http-legacy-revert-design.md`

**Risk note:** Sleep has no automated test framework. Each task's verify step is manual code inspection or partial smoke. The T10 comprehensive smoke is the gate that proves the refactor works in production conditions (CS Client stock launch + Red Robin daemon + verdict round-trip).

**Starting state assumption:** `dev-juan` HEAD is `1d8143f` (the spec commit). The working tree may contain uncommitted experimental edits to `adapters/cobaltstrike/redrobin.cna` from the 2026-05-15/16 smoke session (HTTP/1.1 version line, `.*` regex workarounds, GET helper line removal, DEBUG warn removal). T0 resets the working tree to HEAD so subsequent tasks have a known starting state.

---

## File Structure

| Path | Action | Responsibility |
|------|--------|----------------|
| `adapters/cobaltstrike/redrobin.cna` | Modify | Rewrite HTTP helpers + migrate regex + relax Java floor |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify | Append "CNA HTTP legacy-revert smoke" section |
| `docs/operator-setup.md` | Modify | Remove `--add-opens` claim; document stock launch |

No new files. All changes contained to one Sleep script + two docs.

---

## Task 0: Reset working tree to HEAD

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (revert uncommitted edits)

The 2026-05-15/16 smoke session left uncommitted experimental edits in the working tree. T1-T6 will fully replace those sections, but starting from a clean HEAD makes each later task's "current state" predictable.

- [ ] **Step 1: Verify current branch and HEAD**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git branch --show-current
git rev-parse --short HEAD
```

Expected output: `dev-juan` and `1d8143f` (or later if other commits landed).

- [ ] **Step 2: Inspect working tree changes**

```bash
git status -s adapters/cobaltstrike/redrobin.cna
```

If output contains `M adapters/cobaltstrike/redrobin.cna`, the working tree has uncommitted experimental edits. Continue to Step 3.

If output is empty, the working tree is already clean — skip Step 3 and proceed to Task 1.

- [ ] **Step 3: Discard the experimental edits**

```bash
git checkout -- adapters/cobaltstrike/redrobin.cna
```

Verify the file matches HEAD:

```bash
git diff HEAD adapters/cobaltstrike/redrobin.cna
```

Expected: no output (file matches HEAD).

- [ ] **Step 4: No commit needed**

This task only discards uncommitted working-tree changes. No commit. Proceed to Task 1.

---

## Task 1: Remove java.net.http and java.time.Duration imports

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna:18-21`

- [ ] **Step 1: Read the current imports block**

Open the file and confirm lines 18-21 read exactly:

```sleep
import java.net.*;
import java.net.http.*;
import java.io.*;
import java.time.Duration;
```

- [ ] **Step 2: Remove the two unused imports**

Replace lines 18-21 with:

```sleep
import java.net.*;
import java.io.*;
```

`java.net.URL`, `java.net.URLConnection`, `java.net.HttpURLConnection` come from `java.net.*`. Stream readers/writers (`InputStreamReader`, `OutputStreamWriter`, `BufferedReader`) come from `java.io.*`.

- [ ] **Step 3: Verify**

```bash
grep -n "^import" adapters/cobaltstrike/redrobin.cna
```

Expected output: exactly 2 lines:
- `18:import java.net.*;`
- `19:import java.io.*;`

No mention of `java.net.http` or `java.time.Duration`.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): remove java.net.http and Duration imports

Pre-work for reverting the HTTP layer to URL + HttpURLConnection per
docs/superpowers/specs/2026-05-17-cna-http-legacy-revert-design.md.
The legacy java.net API does not need java.net.http.* or
java.time.Duration. Removing these imports first means the next
tasks can replace the helpers without dangling references."
```

---

## Task 2: Remove $REDROBIN_HTTP_CLIENT global and redrobin_init_http_client sub

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (globals block at line 21, init sub around line 285, boot call around line 295)

The HttpURLConnection pattern doesn't use a long-lived client object — each call opens a fresh `URLConnection`. So we remove the singleton global, the init sub, and the boot-block init call.

- [ ] **Step 1: Read the current globals block**

Around line 21-25, confirm:

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID $REDROBIN_HTTP_CLIENT');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
$REDROBIN_HTTP_CLIENT = $null;
```

- [ ] **Step 2: Remove the $REDROBIN_HTTP_CLIENT global declaration and init**

Replace the block above with:

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
```

- [ ] **Step 3: Remove the redrobin_init_http_client sub**

Search for `sub redrobin_init_http_client`:

```bash
grep -n "redrobin_init_http_client" adapters/cobaltstrike/redrobin.cna
```

Locate the entire sub (typically 5-7 lines, including the `# ===== HttpClient singleton =====` comment block above it). Delete the comment block AND the sub body.

The block to delete looks like this (line numbers may vary):

```sleep
# ===== HttpClient singleton =====
# Built once at script load, reused for every sync/async HTTP call.
# Thread-safe per Java 11+ docs (supports concurrent send() from forks).
sub redrobin_init_http_client {
    local('$builder');
    $builder = [HttpClient newBuilder];
    [$builder connectTimeout: [Duration ofMillis: 200]];
    $REDROBIN_HTTP_CLIENT = [$builder build];
}
```

After deletion: `grep` for `redrobin_init_http_client` returns nothing.

- [ ] **Step 4: Remove the boot-block call**

In the boot block (currently near line 295), the body should read:

```sleep
redrobin_init_http_client();
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake_with_retry();
```

Delete the first line. The new boot body is:

```sleep
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake_with_retry();
```

- [ ] **Step 5: Verify**

```bash
grep -n "REDROBIN_HTTP_CLIENT\|redrobin_init_http_client\|HttpClient" adapters/cobaltstrike/redrobin.cna
```

Expected output: no matches.

- [ ] **Step 6: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): remove HttpClient singleton global + init sub

Each call site in the new legacy-revert pattern opens its own
URLConnection — no long-lived client object needed. Removes the
\$REDROBIN_HTTP_CLIENT global, the redrobin_init_http_client sub,
and the boot-block call.

At this commit redrobin_http_post and redrobin_http_get still
reference the (now undefined) \$REDROBIN_HTTP_CLIENT and will fail
at runtime. Task 3 and Task 4 rewrite those helpers."
```

---

## Task 3: Rewrite redrobin_http_post with URL + HttpURLConnection (KEYSTONE)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — replace the entire `redrobin_http_post` sub

This is one of two keystone tasks. After this commit, handshake POSTs use the legacy API and the `--add-opens java.net.http/jdk.internal.net.http=ALL-UNNAMED` flag becomes unnecessary for POST traffic.

- [ ] **Step 1: Locate the current implementation**

```bash
grep -n "sub redrobin_http_post" adapters/cobaltstrike/redrobin.cna
```

Open the sub (currently uses `HttpClient.send` + `HttpResponse.body`). The sub body spans from `sub redrobin_http_post {` to its matching `}`.

- [ ] **Step 2: Replace the entire sub**

Replace from `# ===== HTTP POST helper` (the comment header above the sub) through the closing `}` of the sub with:

```sleep
# ===== HTTP POST helper (blocking; ~150ms target on healthy daemon) =====
# Uses java.net.URL + HttpURLConnection. Every method called is declared
# on java.net.URLConnection or java.net.HttpURLConnection (public
# abstract classes in the exported java.net package). Sleep's reflection
# access check passes without any --add-opens JVM flag.
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

- [ ] **Step 3: Verify behavioral preservation**

Read the new sub once and confirm:

1. Method calls only target `java.net.*` or `java.io.*` types (see audit table in spec §5.4).
2. Catch block calls `redrobin_sanitize_error` on every error path.
3. Returns `""` on any exception (preserves fail-open semantics for `redrobin_query_quick`).
4. Returns the response body string on success (caller-side `hasmatch` will extract `adapter_session_id`).
5. Timeouts: 200ms connect, 200ms read (matches spec §5.4).
6. POST body and headers match the prior version byte-for-byte at the wire.

- [ ] **Step 4: Manual syntax sanity check**

The file must still have matching braces and the file should not contain syntax that breaks Sleep parsing. Sleep has no syntax linter; the best check is that grep returns reasonable surrounding context:

```bash
grep -B 1 -A 50 "sub redrobin_http_post" adapters/cobaltstrike/redrobin.cna | head -55
```

Confirm the output shows the new sub with the new imports and the catch block.

- [ ] **Step 5: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): rewrite redrobin_http_post using URL+HttpURLConnection

Replaces java.net.http.HttpClient (interface-based API, requires
--add-opens java.net.http/jdk.internal.net.http=ALL-UNNAMED on the
operator JVM) with java.net.URL + HttpURLConnection (abstract-class-
based API with concrete inherited methods declared in the exported
java.net package).

Why this fixes the access-check issue: Sleep dispatches via
obj.getClass().getMethod(). The returned Method's declaringClass is
what the JVM access-checks. With HttpClient, the impl classes
(HttpResponseImpl, HttpRequestBuilderImpl) override interface
methods, so getMethod returns Methods in jdk.internal.net.http
(non-exported). With HttpURLConnection, the impl in sun.* inherits
without overriding, so getMethod returns Methods in java.net.*
(exported public). No --add-opens needed.

Pattern verified by:
  - mgeeky/2d7f8c2a6ffbfd23301e1e2de0312087 (production HTTP precedent)
  - NVISO Labs 2021-03-05 blog (production CS HTTP precedent)

Wire shape (POST body, headers, path) byte-identical to prior version.
Fail-open semantics preserved: returns empty string on any exception.

At this commit, handshake POSTs work without --add-opens. Heartbeat
(GET) still uses the HttpClient code path and needs Task 4 to be
fully flag-free."
```

---

## Task 4: Rewrite redrobin_http_get with URL + HttpURLConnection (KEYSTONE)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — replace the entire `redrobin_http_get` sub

Second keystone. After this commit, heartbeat GETs use the legacy API and the `--add-opens` flag is no longer required for any HTTP path.

- [ ] **Step 1: Locate the current implementation**

```bash
grep -n "sub redrobin_http_get" adapters/cobaltstrike/redrobin.cna
```

Open the sub. Current body uses `HttpClient.send` with `BodyHandlers.ofString`.

- [ ] **Step 2: Replace the entire sub**

Replace from `# ===== HTTP GET helper` (the comment header above the sub) through the closing `}` of the sub with:

```sleep
# ===== HTTP GET helper =====
# Uses java.net.URL + HttpURLConnection. Same module-access reasoning
# as redrobin_http_post.
#
# Returns %(code => N, body => "..."). code=0 means "no response"
# (network error); code>=400 means daemon answered but rejected.
# Caller chooses how to react to each.
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

- [ ] **Step 3: Verify behavioral preservation**

1. Hash return shape `%(code => N, body => "...")` matches prior version.
2. `code = 0` on network error preserves the "daemon unreachable" warn path in `redrobin_heartbeat`.
3. `code >= 400` returned with body skipped (we only read body on 2xx) — heartbeat's `if ($code != 200)` warn still fires for 4xx/5xx.
4. 1000ms connect/read timeouts match prior version.
5. Bearer Authorization header attached.
6. Wire request matches `GET /v1/adapter/heartbeat?adapter=cs-main HTTP/1.1` byte-for-byte.

- [ ] **Step 4: Manual syntax sanity check**

```bash
grep -B 1 -A 30 "sub redrobin_http_get" adapters/cobaltstrike/redrobin.cna | head -35
```

Confirm output shows the new sub.

- [ ] **Step 5: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): rewrite redrobin_http_get using URL+HttpURLConnection

Same legacy-API rationale as Task 3 for redrobin_http_post: every
method called is declared on java.net.URLConnection or
java.net.HttpURLConnection (exported public abstract classes), so
Sleep's reflection access check passes without --add-opens.

getResponseCode() is declared on java.net.HttpURLConnection (public
abstract). getInputStream() is declared on java.net.URLConnection.

Hash return shape (%(code => N, body => '...')) byte-identical to
prior version. 1000ms timeouts unchanged. Bearer auth header
unchanged. Wire request unchanged.

At this commit, both POST and GET use the legacy pattern. The
operator can now load redrobin.cna on a stock CS Client launch
(no JVM flags). Remaining tasks clean up regex semantics and docs."
```

---

## Task 5: Migrate handshake regex from ismatch to hasmatch

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — the `redrobin_handshake_with_retry` sub

Sleep's `ismatch` is full-string anchored (`Matcher.matches()`). `hasmatch` is substring search (`Matcher.find()`). The handshake regex needs substring semantics — the response body is `{"adapter_session_id":"ses_..."}` and the regex must match the inner field.

- [ ] **Step 1: Locate the handshake sub**

```bash
grep -n "sub redrobin_handshake_with_retry" adapters/cobaltstrike/redrobin.cna
```

Find the `if ($response ismatch` line inside the sub (currently uses `'"adapter_session_id":"(ses_[^"]+)"'`).

- [ ] **Step 2: Change ismatch to hasmatch**

Replace:

```sleep
        if ($response ismatch '"adapter_session_id":"(ses_[^"]+)"') {
```

With:

```sleep
        if ($response hasmatch '"adapter_session_id":"(ses_[^"]+)"') {
```

Only the operator name changes from `ismatch` to `hasmatch`. The regex pattern, capture group, and surrounding lines (the `matched()[0]` access, the println, the return) all stay identical.

- [ ] **Step 3: Verify**

```bash
grep -n "ismatch\|hasmatch" adapters/cobaltstrike/redrobin.cna | grep -v "^#"
```

The grep should show no `ismatch` lines that we care about for HTTP response parsing. Verdict regexes (next task) will still show as `ismatch` until Task 6.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "fix(cna): use hasmatch for handshake session_id extraction

Sleep's ismatch operator wraps Matcher.matches() (full-string anchored).
hasmatch wraps Matcher.find() (substring search). The handshake
response body is {\"adapter_session_id\":\"ses_...\"} — wrapped by
braces that the regex never covered.

Per Sleep 2.1 Manual section 6 (Regular Expressions):
'The ismatch predicate anchors itself to the beginning and end of a
string by default. The cousin to ismatch is the hasmatch predicate.
hasmatch looks for a substring within the string that satisfies the
specified pattern.'

This bug was masked during the prior HttpClient refactor because
the body() read was failing silently (catch-block swallowed the
exception). After Tasks 3-4, the body reads cleanly, exposing the
regex semantics issue. hasmatch is the idiomatic Sleep operator for
this case."
```

---

## Task 6: Migrate verdict regexes from ismatch to hasmatch

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — the `redrobin_query_quick` sub

Same root cause as Task 5, but applied to the four verdict regexes (BLOCK/WARN/ESCALATE/reason). This is a latent bug fix: with `ismatch`, the verdict regexes never matched the daemon's JSON-wrapped body, so every verdict silently fell through to the PASS default. The pre-flight gate was effectively a no-op.

- [ ] **Step 1: Locate the verdict regex block**

```bash
grep -n "sub redrobin_query_quick\|ismatch '\"action" adapters/cobaltstrike/redrobin.cna
```

Find the four `ismatch` lines that parse the daemon's verdict response.

- [ ] **Step 2: Change four ismatch instances to hasmatch**

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

Replace the entire block with:

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

Four `ismatch` → `hasmatch` (BLOCK, WARN's reason, ESCALATE, plus the BLOCK's reason and WARN's reason inner matches make 6 total). Updated comment explains the latent bug.

- [ ] **Step 3: Verify**

```bash
grep -n "ismatch" adapters/cobaltstrike/redrobin.cna
```

Expected: no matches for `ismatch` in any user-facing regex check. Comments may still mention `ismatch` for context — that's fine.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "fix(cna): use hasmatch for verdict regex parsing (latent bug)

The four verdict regexes (BLOCK, WARN, ESCALATE, reason capture) all
used ismatch (full-string anchored, Matcher.matches()) but the daemon
response is {\"verdict\":\"BLOCK\",\"reason\":\"...\"} wrapped by
braces and other JSON fields. ismatch never matched, so every verdict
silently fell through to PASS — the pre-flight gate was a no-op.

This bug was invisible during prior smoke testing because:
  1. The HttpClient refactor's body() read was failing first (catch-
     block swallow), so the gate never received a non-empty response
     in practice
  2. Even when the body did read, PASS-by-default is the safe
     fail-open behavior, so nothing looked broken

After Tasks 3-4 fixed the body read, this regex bug surfaced as
'BLOCK rule fires but command dispatches anyway.'

hasmatch is Sleep's substring-search operator (Matcher.find()) per
Sleep 2.1 Manual section 6. Idiomatic fix.

After this commit, real verdict actions (BLOCK/WARN/ESCALATE) reach
the operator's beacon console."
```

---

## Task 7: Relax Java version guard floor 11 → 8

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` — the `redrobin_check_java_version` sub

HttpURLConnection has been stable since JDK 1.1. The Java 11+ floor only existed because HttpClient was Java 11+. Relax to Java 8 as a sensible floor (Sleep + Aggressor predate Java 8). CS 4.12 bundles Java 17, so the guard is defensive for bring-your-own-JVM scenarios.

- [ ] **Step 1: Locate the version-check sub**

```bash
grep -n "sub redrobin_check_java_version" adapters/cobaltstrike/redrobin.cna
```

- [ ] **Step 2: Replace the version-parsing logic**

The current sub does:

```sleep
sub redrobin_check_java_version {
    local('$version $major_str $major');
    $version = [System getProperty: "java.version"];
    if ($version is $null) {
        warn("[Red Robin] cannot read java.version system property; assuming Java 11+");
        return true;
    }
    $major_str = split('\.', $version)[0];
    $major = parseNumber($major_str);
    if ($major < 11) {
        warn("[Red Robin] FATAL: Java " . $version . " detected. Red Robin requires Java 11+.");
        warn("[Red Robin] Upgrade your CS Client JVM (e.g., install Temurin 21) and reload this script.");
        return false;
    }
    return true;
}
```

Replace with the Java 8 version:

```sleep
sub redrobin_check_java_version {
    local('$version $major_str $major');
    $version = [System getProperty: "java.version"];
    if ($version is $null) {
        warn("[Red Robin] cannot read java.version system property; assuming Java 8+");
        return true;
    }

    # Java 8 string format: "1.8.0_392" (major=1, second=8 → treat as 8)
    # Java 9+ string format: "11.0.21" / "17.0.9" / "21.0.2" (major is real)
    $major_str = split('\.', $version)[0];
    $major = parseNumber($major_str);
    if ($major == 1) {
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

Key changes:
- Floor lowered from 11 to 8.
- New branch handles Java 8's `1.8.0_X` version string (where `split('\.', $version)[0]` returns `1`, not `8`).
- Updated FATAL message text.

- [ ] **Step 3: Verify**

```bash
grep -A 25 "sub redrobin_check_java_version" adapters/cobaltstrike/redrobin.cna | head -30
```

Confirm the new logic: handles Java 8 version-string format, FATAL at `< 8`.

Also check the script-top `throw` block that calls this sub:

```bash
grep -A 3 "redrobin_check_java_version" adapters/cobaltstrike/redrobin.cna | head -6
```

Should show the immediate-call check at script top still intact:

```sleep
if (!redrobin_check_java_version()) {
    throw "Red Robin: Java 8+ required (see previous warn lines).";
}
```

If the throw message still says `Java 11+`, update it to `Java 8+` (one-line edit, same sub).

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): relax Java version guard floor 11 -> 8

The Java 11+ floor existed only because HttpClient (java.net.http)
required Java 11+. After Tasks 3-4 reverted to java.net.URL +
HttpURLConnection (stable since JDK 1.1), the floor can drop to
Java 8.

CS 4.12 bundles Java 17 per Fortra's official launcher, so the
guard is defensive for operators running bring-your-own-JVM
configurations on legacy systems.

Added handling for Java 8's '1.8.0_X' version-string format where
split('.')[0] returns '1', not '8'. The new logic peeks at the
second component to recover the real major version.

FATAL message text updated from 'Java 11+' to 'Java 8+'."
```

---

## Task 8: Update docs/operator-setup.md

**Files:**
- Modify: `docs/operator-setup.md`

The 2026-05-14 modernization added a section claiming "No JVM flags required". After Tier 3 smoke that turned out to be false (the refactor needed `--add-opens` for `jdk.internal.net.http`). Now with the legacy-revert, the claim is *actually* true. Update the doc to reflect reality and remove all `--add-opens` references.

- [ ] **Step 1: Read the current Cobalt Strike Client Java requirement section**

```bash
grep -n "Cobalt Strike Client Java requirement\|--add-opens\|Java 11" docs/operator-setup.md
```

Find the existing H3 block added by the 2026-05-14 plan T10 (commit `084814d`).

- [ ] **Step 2: Replace the section with the legacy-revert version**

Find the existing `### Cobalt Strike Client Java requirement` H3 section (roughly 18 lines). Replace it with:

```markdown
### Cobalt Strike Client Java requirement

Red Robin's Aggressor script (`redrobin.cna`) requires **Java 8+** on the CS
Client side. CS 4.12 bundles OpenJDK 17 in its standard launcher (per
[Fortra Java dependency docs](https://www.cobaltstrike.com/help-java-dependency)),
so operators on stock CS launches are automatically on a supported JVM.

**No JVM flags required.** The `.cna` uses `java.net.URL` + `HttpURLConnection`
(JDK 1.1+ legacy HTTP API). Sleep's reflection mechanism dispatches via
methods declared on `java.net.URLConnection` and `java.net.HttpURLConnection`
(public abstract classes in the exported `java.net` package), so module
access checks pass without `--add-opens`.

Launch CS Client through the official launcher (no manual `java -jar`):

```
cobaltstrike.exe              # Windows
./cobaltstrike-client         # Linux/Mac
```

If the .cna fails to load with `Java ... detected. Red Robin requires
Java 8+` in the Script Console, your CS Client JVM is older than Java 8.
Upgrade to a modern JDK (Temurin 17 or 21 are well-tested) and reload
the script.

**Historical note:** Earlier Red Robin versions (`dev-juan` commits
`8bb6642`..`084814d`, 2026-05-14) used `java.net.http.HttpClient` and
required `--add-opens java.net.http/jdk.internal.net.http=ALL-UNNAMED`.
That flag is no longer needed and operator launchers should not include
it. See `docs/superpowers/specs/2026-05-17-cna-http-legacy-revert-design.md`
for the rationale.
```

- [ ] **Step 3: Verify**

```bash
grep -n "add-opens\|java.net.http\|HttpClient" docs/operator-setup.md
```

Expected: only the historical note mention. No active advice to use `--add-opens`.

- [ ] **Step 4: Commit**

```bash
git add -f docs/operator-setup.md
git commit -m "docs(operator-setup): remove --add-opens flag advice

After Tasks 3-4 reverted the HTTP layer to URL + HttpURLConnection
(2026-05-17-cna-http-legacy-revert-design spec), no JVM flags are
required. The 2026-05-14 H3 section added by commit 084814d falsely
claimed 'No JVM flags required' but actually shifted the flag from
--add-opens java.base/sun.net.www.protocol.http to --add-opens
java.net.http/jdk.internal.net.http — the operator still needed one.

Updated text:
  - Java floor lowered from 11+ to 8+ (HttpURLConnection is JDK 1.1+)
  - Stock launcher path documented (cobaltstrike.exe, no manual java -jar)
  - Historical note preserved for operators upgrading from a prior
    Red Robin version that documented the workaround
  - Cross-reference to the legacy-revert design spec for rationale"
```

---

## Task 9: Append legacy-revert smoke section to test_cna_smoke.md

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md`

The existing "CNA HTTP modernization smoke" section (added by 2026-05-14 plan T9) used a `java --add-opens ... -jar` launch command. With the legacy-revert, stock-launch is the test. Add a new section.

- [ ] **Step 1: Read the current smoke doc structure**

```bash
tail -10 adapters/cobaltstrike/tests/test_cna_smoke.md
```

Confirm the file ends with the modernization smoke section.

- [ ] **Step 2: Append the new section**

Append the following content to the end of the file (preserve the existing modernization section above it):

````markdown

---

## CNA HTTP legacy-revert smoke (added 2026-05-17)

These steps verify the refactor in `docs/superpowers/specs/2026-05-17-cna-http-legacy-revert-design.md`. Supersedes the prior modernization smoke section above.

### 1. Stock launch (no JVM flags)

The keystone test. Operator launches CS Client via the official launcher with **zero JVM flags**.

- [ ] Close any running CS Client.
- [ ] Launch CS Client via the stock launcher:
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
- [ ] In daemon log expect: `POST /v1/adapters/connect HTTP/1.1 200 OK`
- [ ] In daemon log expect: NO `Unsupported upgrade request` warnings (HttpURLConnection is HTTP/1.1 only — no h2c upgrade attempts).

### 2. Heartbeat round-trip

- [ ] Wait 30-40 seconds after .cna load.
- [ ] In daemon log expect: `GET /v1/adapter/heartbeat?adapter=cs-main HTTP/1.1 200 OK`
- [ ] In Script Console expect: NO `Attempted to call a non-static method on a null reference` errors at the heartbeat line numbers.

### 3. Verdict round-trip (regex fix verification)

- [ ] `&binit(rand(100000))` in Script Console → registers dummy bid.
- [ ] Type alias `mimikatz` in beacon console.
- [ ] In daemon log expect: `POST /v1/verdict/quick HTTP/1.1 200 OK`
- [ ] In beacon console expect: `[!] [Red Robin] DANGER: <reason from rule>` (BLOCK or WARN — the rule fires and the hasmatch regex extracts the action). NOT a silent dispatch (which would indicate the regex bug from Task 6 is still present).

### 4. Actionable error logging

- [ ] Stop the daemon (`redrobin daemon stop`).
- [ ] Type any aliased command in the beacon console.
- [ ] In Script Console expect: `[Red Robin] daemon connection refused at http://localhost:7777 — is the daemon running?`
- [ ] Restart daemon. Next aliased command must succeed without warn.

### 5. Token sanitization (strengthened TC)

Strengthens the prior TC5 which used a connect-refused path that did not actually exercise the sanitization regex.

- [ ] In a separate terminal, start a Python one-liner HTTP server that echoes request headers:
  ```bash
  python -c "from http.server import BaseHTTPRequestHandler,HTTPServer
  class H(BaseHTTPRequestHandler):
      def do_POST(self):
          self.send_response(401);self.send_header('Content-Type','application/json');self.end_headers()
          self.wfile.write(f'{{\"detail\":\"req-headers={dict(self.headers)}\"}}'.encode())
  HTTPServer(('127.0.0.1',9999),H).serve_forever()"
  ```
- [ ] In CS Client Script Console: `$REDROBIN_URL = "http://127.0.0.1:9999"; redrobin_handshake_with_retry();`
- [ ] In Script Console expect: a warn line with `[Red Robin] daemon returned HTTP 4xx/5xx` text.
- [ ] In Script Console verify: the warn message does NOT contain the literal token characters. Look for `***REDACTED***` placeholders or simply confirm no `Bearer wT-gS6Pi...` pattern.
- [ ] Restore `$REDROBIN_URL = "http://localhost:7777"` and Ctrl+C the Python server.

### 6. Handshake retry under daemon-boot race

- [ ] Stop daemon (`redrobin daemon stop`).
- [ ] Aggressor Scripts → Unload `redrobin.cna` → Load again.
- [ ] Within 1-2 seconds: `redrobin daemon start --host 127.0.0.1 --port 7777`
- [ ] Watch the Script Console — expect to see:
  ```
  [Red Robin] handshake attempt 1/3 failed; retrying in 500ms
  [Red Robin] handshake OK; session=ses_<uuid>
  ```
  (Or attempt 2 then attempt 3 then success, depending on timing.)

### 7. Java version guard (optional smoke)

Skip if no easy way to fake Java version. Otherwise:

- [ ] Temporarily hardcode `$major = 7;` in `redrobin_check_java_version`.
- [ ] Reload .cna.
- [ ] In Script Console expect: `[Red Robin] FATAL: Java ... detected. Red Robin requires Java 8+.`
- [ ] Confirm `[Red Robin] handshake OK` does NOT appear (script aborted at throw).
- [ ] Revert the test edit.
````

- [ ] **Step 3: Verify the file ends with the new section**

```bash
tail -5 adapters/cobaltstrike/tests/test_cna_smoke.md
```

Should show the last lines of the Java version guard subsection.

- [ ] **Step 4: Commit**

```bash
git add -f adapters/cobaltstrike/tests/test_cna_smoke.md
git commit -m "docs(test-smoke): add CNA HTTP legacy-revert verification steps

Supersedes the prior 'CNA HTTP modernization smoke' section above.
Same 7 sub-steps adapted to the legacy-revert pattern:

  - TC1 uses stock cobaltstrike.exe launcher (no JVM flags) instead
    of 'java --add-opens ... -jar' invocation
  - TC3 explicitly tests the hasmatch regex fix from Task 6 by
    checking that BLOCK/WARN/ESCALATE actually reach the beacon
    console (not a silent PASS fall-through)
  - TC5 strengthens token sanitization with a real 401 path (Python
    one-liner echo server) instead of the prior weak connect-refused
    path
  - TC1 explicitly checks the absence of 'Unsupported upgrade request'
    daemon warnings (HttpURLConnection is HTTP/1.1 only)
  - TC7 (Java version guard) updated for the Java 8+ floor"
```

---

## Task 10: Comprehensive end-to-end smoke validation

This is the gate task — running through every section of the new "CNA HTTP legacy-revert smoke" doc on a real CS Client + Red Robin daemon + teamserver.

**Files:** None modified. This is verification only.

- [ ] **Step 1: Ensure prerequisites**

- CS REST API running on teamserver (`100.103.238.211:50443`). After a droplet reboot, csrestapi must be respawned per `red-team-playground/docs/runbooks/cs-restapi-bringup.md` §"Manual workaround". Verify with:
  ```bash
  ssh root@100.103.238.211 'ss -tlnp | grep -E ":(50050|50443)"'
  ```
  Both ports must listen. If `50443` is missing, run the runbook's csrestapi spawn block.
- Red Robin daemon running (`redrobin daemon start` from `C:\Users\jcspi\redrobin-smoke`).
- `redrobin doctor` returns all `[OK]` (including `cs-adapter`).
- CS Client is closed (we want a fresh launch to validate stock-launch behavior).

- [ ] **Step 2: Run the full legacy-revert smoke checklist**

Open `adapters/cobaltstrike/tests/test_cna_smoke.md`, navigate to the "CNA HTTP legacy-revert smoke (added 2026-05-17)" section, and execute each of the 7 sub-steps in order. Check each `[ ]` box as you complete it.

The keystone is TC1: stock launch must succeed with no JVM flags. If TC1 fails, the entire refactor fails — STOP and root-cause before proceeding.

- [ ] **Step 3: Capture failures**

If any TC fails, do NOT mark this task complete. Instead:

- Identify which task (T1-T9) introduced the regression. If it's an unanticipated issue, see spec §10 Rollback.
- Fix forward with a new commit (do NOT amend prior commits — they're already pushed).
- Re-run the failing TC.

- [ ] **Step 4: Mark all task checkboxes complete + push**

When all 7 smoke sub-steps pass, the implementation is done.

```bash
git push origin dev-juan
```

---

## Self-Review (skill checklist)

**Spec coverage:** Every section of the spec has at least one corresponding task:
- §5.1 Imports → Task 1
- §5.2 Globals → Task 2
- §5.3 Sanitize helper → unchanged from T3 of prior plan (preserved by not editing)
- §5.4 POST helper → Task 3 (keystone)
- §5.5 GET helper → Task 4 (keystone)
- §5.6 JSON helpers → unchanged from prior plan (preserved by not editing)
- §5.7 Handshake retry regex → Task 5
- §5.8 Verdict regex → Task 6
- §5.9 Heartbeat → unchanged (its caller GET helper changed via Task 4)
- §5.10 Java version guard → Task 7
- §5.11 Boot block → covered by Task 2's removal of init call
- §6 Behavior contracts → preserved in Tasks 3/4 (verify steps)
- §7 Java compatibility → enforced in Task 7
- §8 Security considerations → reused via existing sanitize helper + explicit UTF-8 in Tasks 3/4
- §9 Test plan → Task 9 (smoke doc update) + Task 10 (run it)
- §10 Rollback plan → covered by per-task `git revert` simplicity (referenced in Task 10 Step 3)
- §11 Implementation order → matches Tasks 1-9 order plus T0 preamble plus T10 gate
- §12 Open questions → Q1 (keep version guard) and Q5 (defer JAR helper) resolved by Task 7 + non-goals; Q2-Q4 carried as deferred.

**Placeholder scan:** No TBDs, no "implement later" markers, no "similar to Task N" — each task contains the complete code to write and the exact commit message text.

**Type consistency:**
- `redrobin_http_post(path, body) -> String` declared in Task 3, called from `redrobin_handshake_with_retry` (Task 5) and `redrobin_query_quick` (Task 6). ✓
- `redrobin_http_get(path) -> %(code, body)` declared in Task 4, called from `redrobin_heartbeat` (unchanged, calls preserved by Task 4's hash return shape). ✓
- `redrobin_sanitize_error($e) -> String` from prior plan, called from Task 3's POST helper catch block. ✓
- `redrobin_check_java_version() -> bool` updated in Task 7, called at script-top throw block which is also updated in Task 7 Step 3. ✓
- Java method signatures audited in spec §5.4 table — all `java.net.*` and `java.io.*` types only. ✓

All references consistent. Ready for execution.
