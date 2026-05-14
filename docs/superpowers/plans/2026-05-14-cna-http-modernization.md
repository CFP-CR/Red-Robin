# CNA HTTP Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `URL.openConnection()` + sun.* reflection in `adapters/cobaltstrike/redrobin.cna` with `java.net.http.HttpClient` so the .cna loads without `--add-opens` JVM flags on Java 11+. Add Java version guard, actionable error logging, token sanitization, and handshake retry along the way.

**Architecture:** Single `HttpClient` singleton initialized at script load; thread-safe, reused for sync POST/GET and fork{}-wrapped async calls. Java 11+ floor enforced via `throw` at top of script. Behavior contract preserved byte-identical (fail-open semantics, wire shape, fork patterns).

**Tech Stack:** Sleep (Aggressor scripting), Java 11+ `java.net.http` package (`HttpClient`, `HttpRequest`, `HttpResponse`), `java.time.Duration`.

**Spec:** `docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md`

**Risk note:** Sleep has no automated test framework. Each task's "verify" step is manual code inspection or partial smoke test. The comprehensive end-to-end smoke (Task 9) is the gate that proves the refactor works in production conditions.

---

## File Structure

| Path | Action | Responsibility |
|------|--------|----------------|
| `adapters/cobaltstrike/redrobin.cna` | Modify | Main refactor — replace HTTP layer + add helpers |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify | Update verification steps to match new helpers |
| `docs/operator-setup.md` | Modify | Note Java 11+ requirement + remove any `--add-opens` mention if present |

No new files. All changes contained to one Sleep script + two docs.

---

## Task 1: Add imports for java.net.http and Duration

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna:18-19`

- [ ] **Step 1: Read the current imports block**

Open `adapters/cobaltstrike/redrobin.cna` and confirm lines 18-19 read exactly:

```sleep
import java.net.*;
import java.io.*;
```

- [ ] **Step 2: Add the new imports**

Replace lines 18-19 with:

```sleep
import java.net.*;
import java.net.http.*;
import java.io.*;
import java.time.Duration;
```

Note: nested static classes (`HttpRequest$BodyPublishers`, `HttpResponse$BodyHandlers`) are NOT imported here — they will be referenced with fully-qualified names at call sites per spec §5.2 nested-class note. Sleep's `$` is reserved for variable interpolation and unreliable inside `import` statements.

- [ ] **Step 3: Verify no behavior change**

Run a syntax sanity check (no automated linter available; inspect manually):
- File still has matching braces.
- All four import lines are at the top before any `global` or `sub` declaration.
- No typos.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): add imports for java.net.http and Duration

Pre-work for the HttpClient-based HTTP refactor. Sleep imports here
are wildcard-only; nested static classes (BodyPublishers, BodyHandlers)
will be referenced via fully-qualified names at call sites in later
tasks because Sleep cannot reliably resolve nested class syntax in
import statements (the \$ separator collides with Sleep's variable
interpolation operator).

Behavior unchanged — these are additional imports, no removals."
```

---

## Task 2: Add `$REDROBIN_HTTP_CLIENT` global

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna:21-24`

- [ ] **Step 1: Read the current globals block**

Confirm lines 21-24 read:

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
```

- [ ] **Step 2: Add the HttpClient global**

Replace lines 21-24 with:

```sleep
global('$REDROBIN_URL $REDROBIN_TOKEN $REDROBIN_SESSION_ID $REDROBIN_HTTP_CLIENT');
$REDROBIN_URL = "http://localhost:7777";
$REDROBIN_TOKEN = "";
$REDROBIN_SESSION_ID = "";
$REDROBIN_HTTP_CLIENT = $null;
```

- [ ] **Step 3: Verify**

The global declaration must use single quotes (Sleep convention) and space-separate the variable names. Initial value `$null` is correct — the actual `HttpClient` instance is built in Task 4.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): declare \$REDROBIN_HTTP_CLIENT global

Reserves the global slot for the HttpClient singleton that Task 4
will construct. Initial value is \$null so any premature read (e.g.,
from a partial reload) fails-loud rather than silently using a wrong
value."
```

---

## Task 3: Add `redrobin_sanitize_error` sub

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (insert new sub between lines 76 and 79, after `redrobin_escape_json` and before the integrity-level section)

- [ ] **Step 1: Locate the insertion point**

Find the closing `}` of `redrobin_escape_json` (currently line 76). Insert the new sub immediately after the blank line that follows it (before line 79's comment block `# ===== Integrity level (Slice 11) =====`).

- [ ] **Step 2: Add the sub**

Insert this block after line 77 (the blank line after `redrobin_escape_json`):

```sleep
# ===== Error sanitization =====
# Strip bearer token + token value from exception messages before logging.
# The Aggressor Script Console appears in engagement screenshots / shared
# screens; an exception's toString() can include request headers in some
# JVMs. Wrap every exception via this sub before printing.
sub redrobin_sanitize_error {
    local('$err_str');
    $err_str = "" . $1;
    if (strlen($REDROBIN_TOKEN) > 8) {
        $err_str = replace($err_str, $REDROBIN_TOKEN, '***REDACTED***');
    }
    $err_str = replace($err_str, 'Bearer [A-Za-z0-9_\-\.=]+', 'Bearer ***REDACTED***');
    return $err_str;
}
```

- [ ] **Step 3: Verify**

- The sub is positioned after `redrobin_escape_json` and before `redrobin_integrity_level`.
- The two `replace()` calls use Java regex (this is documented in spec §8 — current Red Robin tokens are URL-safe base64 and don't contain regex metacharacters outside character classes, so this is safe).
- The literal-token replacement runs FIRST (so the catch-all `Bearer` pattern doesn't redact a value that's already been replaced).

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): add redrobin_sanitize_error helper

Strips the bearer token (both literal value and 'Bearer ...' regex
catch-all) from exception messages before they reach the Aggressor
Script Console. Defensive layer against secrets appearing in
engagement screenshots / screen shares.

Future tasks (POST/GET helpers) call this on every exception path."
```

---

## Task 4: Add `redrobin_init_http_client` sub + wire into boot

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (insert new sub before line 194's `# ===== Boot =====` comment; modify boot block at line 195-196)

- [ ] **Step 1: Add the init sub**

Insert this block immediately before line 194 (`# ===== Boot =====`):

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

(Two trailing blank lines to match the surrounding spacing.)

- [ ] **Step 2: Wire init into boot**

Modify the boot block. Currently lines 195-196 read:

```sleep
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake();
```

Insert the init call as the first line of the boot block:

```sleep
redrobin_init_http_client();
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake();
```

- [ ] **Step 3: Verify**

- `redrobin_init_http_client` runs BEFORE `redrobin_handshake` so the handshake (still using the OLD `redrobin_http_post` at this point) is unaffected.
- The OLD `redrobin_http_post` does NOT use `$REDROBIN_HTTP_CLIENT` yet — we wire that in Task 5.
- Loading the .cna at this commit (with `--add-opens` workaround if needed) should produce the existing handshake behavior plus a (silent) HttpClient instance ready for Task 5.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): add HttpClient singleton init at boot

Builds the HttpClient with connectTimeout=200ms once at script load
and stores it in \$REDROBIN_HTTP_CLIENT. Subsequent HTTP helpers
(Task 5+) will reuse this instance — thread-safe per Java 11 docs,
supports concurrent send() from forks.

Existing redrobin_http_post (using URL.openConnection) is unchanged
in this commit; the new client is built but not yet used."
```

---

## Task 5: Rewrite `redrobin_http_post` to use HttpClient

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna:38-63` (the `redrobin_http_post` sub)

- [ ] **Step 1: Locate the current implementation**

Lines 38-63 contain the legacy implementation. Read it once to confirm the comment header (line 38) and the closing brace (line 63).

- [ ] **Step 2: Replace the entire sub**

Replace lines 38-63 with:

```sleep
# ===== HTTP POST helper (blocking; ~150ms target on healthy daemon) =====
# Uses java.net.http.HttpClient (public API since Java 11). No sun.*
# reflection — works on stock CS Client launches without --add-opens.
# On any exception: returns "" (preserves fail-open caller contract)
# and logs an actionable error via warn().
sub redrobin_http_post {
    local('$path $body $reqBuilder $request $response $err');
    ($path, $body) = @_;

    $reqBuilder = [HttpRequest newBuilder];
    [$reqBuilder uri: [URI create: $REDROBIN_URL . $path]];
    [$reqBuilder timeout: [Duration ofMillis: 200]];
    [$reqBuilder header: "Authorization": "Bearer " . $REDROBIN_TOKEN];
    [$reqBuilder header: "Content-Type": "application/json"];
    [$reqBuilder POST: [java.net.http.HttpRequest$BodyPublishers ofString: $body]];
    $request = [$reqBuilder build];

    try {
        $response = [$REDROBIN_HTTP_CLIENT send: $request: [java.net.http.HttpResponse$BodyHandlers ofString]];
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

- [ ] **Step 3: Verify behavioral preservation**

- The caller `redrobin_query_quick` (line 98-127) does `ismatch '"action":"BLOCK"'` etc. on the returned string. Empty string `""` matches nothing → falls through to `return %(action => "PASS")` (line 124-126). Fail-open semantics preserved.
- POST body, headers, path are byte-identical to the legacy implementation.
- Caller `redrobin_handshake` (line 170-191) uses `$response ismatch '"adapter_session_id":"(ses_[^"]+)"'` — same regex pattern works on the new response.

- [ ] **Step 4: Verify .cna still loads (stock CS Client, no --add-opens)**

This is the first task where the bug should be FIXED. Manual smoke:

1. Close any running CS Client.
2. Launch CS Client without `--add-opens` flags.
3. Connect to the teamserver.
4. Load the .cna via Aggressor Scripts → Load.
5. Watch the Script Console for:
   - ❌ NO `cannot access sun.net.www.protocol.http` errors
   - ❌ NO `Attempted to call a non-static method on a null reference`
   - ✅ `[Red Robin] handshake OK; session=ses_<uuid>` (handshake still uses old single-shot logic — that's fine for now, retry comes in Task 7)
6. In the daemon log, confirm `POST /v1/adapters/connect HTTP/1.1 200 OK` appears within ~1s.

If any of these fail, revert the commit and debug the specific error before proceeding.

- [ ] **Step 5: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): rewrite redrobin_http_post using HttpClient

Replaces URL.openConnection() + sun.* reflection with java.net.http.HttpClient
(public API since Java 11). Effects:

  - .cna loads on stock CS Client without --add-opens JVM flags.
  - Same wire shape (POST + headers + body byte-identical) so daemon
    side has zero changes.
  - Same fail-open semantics: on exception, returns empty string
    which the caller treats as 'no verdict' -> command dispatches.

New behavior:

  - Actionable error logging per failure type (ConnectException,
    timeout, DNS, generic). Operators can diagnose mid-engagement
    instead of staring at silence.
  - All exception messages pass through redrobin_sanitize_error so
    the bearer token never leaks into the Script Console.

Nested static classes (BodyPublishers, BodyHandlers) referenced via
fully-qualified names because Sleep's \$ collides with import syntax."
```

---

## Task 6: Add `redrobin_http_get` helper + refactor `redrobin_heartbeat`

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (add `redrobin_http_get` sub after `redrobin_http_post`; replace `redrobin_heartbeat` body at lines 225-241)

- [ ] **Step 1: Add the GET helper**

Insert this block immediately after the closing `}` of `redrobin_http_post` (right before the `# ===== JSON helpers =====` comment that introduces `redrobin_escape_json`):

```sleep
# ===== HTTP GET helper =====
# Returns %(code => N, body => "..."). code=0 means "no response"
# (network error); code>=400 means daemon answered but rejected.
# Caller chooses how to react to each.
sub redrobin_http_get {
    local('$path $reqBuilder $request $response');
    $path = $1;

    $reqBuilder = [HttpRequest newBuilder];
    [$reqBuilder uri: [URI create: $REDROBIN_URL . $path]];
    [$reqBuilder timeout: [Duration ofMillis: 1000]];
    [$reqBuilder GET];
    $request = [$reqBuilder build];

    try {
        $response = [$REDROBIN_HTTP_CLIENT send: $request: [java.net.http.HttpResponse$BodyHandlers ofString]];
        return %(code => [$response statusCode], body => [$response body]);
    } catch $e {
        return %(code => 0, body => "");
    }
}
```

- [ ] **Step 2: Replace `redrobin_heartbeat`**

Locate the current `redrobin_heartbeat` sub (originally lines 225-241; line numbers may have shifted by ~20 due to previous tasks — search for `sub redrobin_heartbeat`). Replace it with:

```sleep
# ===== Heartbeat — surface daemon outages in the CS event log =====
# Without this loop the operator would silently keep typing while the alias
# gate fails open. Hitting /v1/adapter/heartbeat every 30s gives them a
# visible warning when the daemon goes away mid-engagement.
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

- [ ] **Step 3: Verify**

- The `fork{}` block at the bottom of the script (currently lines 244-249, may have shifted) still calls `redrobin_heartbeat()` — no change needed there. The fork keeps running every 30s, just now calls the new sub.
- `redrobin_http_get` returns a hash; `$result["code"]` extracts the status field.

- [ ] **Step 4: Verify heartbeat works**

After committing, wait 30-35 seconds with the .cna loaded and the daemon running. Daemon log should show:

```
INFO:     127.0.0.1:NNNN - "GET /v1/adapter/heartbeat?adapter=cs-main HTTP/1.1" 200 OK
```

If 5+ minutes pass with no GET in the daemon log, the heartbeat fork loop is broken — check Script Console for any errors.

- [ ] **Step 5: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "refactor(cna): add redrobin_http_get + use it in heartbeat

New redrobin_http_get returns %(code => N, body => '...') so the
caller can distinguish 'no response' (code=0) from 'daemon answered
but bad status' (code>=400). Both useful — heartbeat treats them
differently.

redrobin_heartbeat now calls the helper instead of inlining its own
URL.openConnection / setRequestMethod boilerplate. Same wire request
(GET /v1/adapter/heartbeat?adapter=cs-main), same 1-second timeout,
same warn messages on failure.

Background fork{} loop at end of script unchanged — it still calls
redrobin_heartbeat every 30s."
```

---

## Task 7: Replace `redrobin_handshake` with `redrobin_handshake_with_retry`

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (the `redrobin_handshake` sub at original lines 169-191 + the boot block call site)

- [ ] **Step 1: Locate the current sub**

Search for `sub redrobin_handshake` in the file (line numbers shifted by earlier tasks). Confirm the current body still uses the single-shot pattern (one call to `redrobin_http_post`, no retry).

- [ ] **Step 2: Replace the sub**

Replace the entire `redrobin_handshake` sub with:

```sleep
# ===== Adapter handshake (3-attempt retry with backoff) =====
# Daemon may be still booting when operator loads the .cna. Single-shot
# handshake left the alias gate in a permanently failed state if the
# first attempt missed the boot window. Retry with 500/1000/2000ms
# backoff covers the typical daemon-boot race without being annoying.
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
        if ($response ismatch '"adapter_session_id":"(ses_[^"]+)"') {
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

- [ ] **Step 3: Update the boot block call site**

Find the boot block (it currently reads):

```sleep
redrobin_init_http_client();
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake();
```

Change the last line from `redrobin_handshake();` to `redrobin_handshake_with_retry();`:

```sleep
redrobin_init_http_client();
$REDROBIN_TOKEN = redrobin_load_token();
redrobin_handshake_with_retry();
```

- [ ] **Step 4: Verify retry works**

Manual race-condition test:

1. Stop the Red Robin daemon (`redrobin daemon stop`).
2. In CS Client, Aggressor Scripts → Unload `redrobin.cna`, then immediately Load again.
3. Within 1-2 seconds: start the daemon (`redrobin daemon start --host 127.0.0.1 --port 7777`).
4. Watch the Script Console — expect to see:

```
[Red Robin] handshake attempt 1/3 failed; retrying in 500ms
[Red Robin] handshake OK; session=ses_<uuid>
```

(Or attempt 2 then attempt 3 then success, depending on timing.)

- [ ] **Step 5: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): handshake with 3-attempt exponential backoff

Daemon may still be booting when the operator loads the .cna. The
prior single-shot handshake left the alias gate in a permanently
failed-open state if the first attempt missed the boot window.

New behavior: 3 attempts with 500/1000/2000ms backoff (total budget
~3.5s worst case). Acceptable because handshake runs once at load,
not in the hot path.

Boot block updated to call redrobin_handshake_with_retry() instead
of redrobin_handshake().

Manifest JSON byte-identical to the prior version — no daemon-side
contract change."
```

---

## Task 8: Add Java version guard at top of script

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (insert version guard after globals init, before boot block)

This task is LAST in the implementation order (not first as in the spec architecture) so that earlier tasks can be tested without the guard interfering. The guard goes in last because we want to PROVE the HTTP refactor works before adding the gate.

- [ ] **Step 1: Add the version-check sub**

Insert this block immediately AFTER the `$REDROBIN_HTTP_CLIENT = $null;` line (end of globals init from Task 2) and BEFORE any other sub definitions:

```sleep
# ===== Java version guard =====
# HttpClient (java.net.http.*) is Java 11+ only. CS 4.12 ships with
# Java 17+ but operators on older CS distributions or with bring-your-own
# JVM setups may still have Java 8 — abort with a clear message rather
# than fail with cryptic "Unknown class HttpClient" errors downstream.
sub redrobin_check_java_version {
    local('$version $major_str $major');
    $version = [System getProperty: "java.version"];

    # Defensive: restrictive SecurityManager can deny this property.
    if ($version is $null) {
        warn("[Red Robin] cannot read java.version system property; assuming Java 11+");
        return true;
    }

    # Java 11+: "11.0.21" / "17.0.9" / "21.0.2" / "25.0.1"
    # Java 8 : "1.8.0_392"
    $major_str = split('\.', $version)[0];
    $major = parseNumber($major_str);
    if ($major < 11) {
        warn("[Red Robin] FATAL: Java " . $version . " detected. Red Robin requires Java 11+.");
        warn("[Red Robin] Upgrade your CS Client JVM (e.g., install Temurin 21) and reload this script.");
        return false;
    }
    return true;
}

if (!redrobin_check_java_version()) {
    throw "Red Robin: Java 11+ required (see previous warn lines).";
}


```

(Two trailing blank lines to match surrounding spacing.)

- [ ] **Step 2: Verify guard passes on Java 11+**

Reload the .cna in CS Client (which is running modern Java per spec §7). Script Console must NOT show the FATAL message. Handshake should proceed normally.

- [ ] **Step 3: Verify guard FAILS LOUD on simulated Java 8**

Optional advanced test (skip if no easy way to fake `java.version`):

Edit `redrobin_check_java_version` temporarily to hardcode `$major = 8;` for testing. Reload .cna. Script Console must show:

```
[Red Robin] FATAL: Java <version> detected. Red Robin requires Java 11+.
[Red Robin] Upgrade your CS Client JVM (e.g., install Temurin 21) and reload this script.
```

…and the script must NOT continue past the throw (no `[Red Robin] handshake OK` line). Revert the test edit before committing.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): Java 11+ version guard at script top

HttpClient and Duration are Java 11+. If an operator loads this .cna
into a CS Client running Java 8, the import statements at the top of
the file would otherwise succeed silently (Sleep parses) but every
subsequent HTTP call would throw 'Unknown class HttpClient' — a
confusing cascade for the operator.

New guard:
  - reads java.version via System.getProperty (null-safe)
  - parses leading major version
  - if < 11: prints FATAL + upgrade instructions, then throw

throw (not return) is used because top-level return semantics in
Aggressor are not documented to halt the script — throw is caught by
Aggressor's script-load handler and produces a clean abort with the
exception message visible in the Script Console."
```

---

## Task 9: Update `test_cna_smoke.md` with new verification steps

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md`

- [ ] **Step 1: Read the current smoke doc**

Open `adapters/cobaltstrike/tests/test_cna_smoke.md`. Skim to understand its current structure (it's a markdown checklist for manual verification).

- [ ] **Step 2: Add a new section at the end**

Append the following section to `test_cna_smoke.md`:

````markdown

---

## CNA HTTP modernization smoke (added 2026-05-14)

These steps verify the refactor in `docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md`.

### 1. Stock launch (no --add-opens)

- [ ] Close any running CS Client.
- [ ] Launch CS Client via its default shortcut (no JVM flags):
  ```powershell
  java -jar cobaltstrike-client.jar
  ```
- [ ] Connect to the teamserver, load `redrobin.cna`.
- [ ] In Script Console expect: `[Red Robin] handshake OK; session=ses_<uuid>`
- [ ] In Script Console expect: NO `cannot access sun.net.www.protocol.http`
- [ ] In Script Console expect: NO `Attempted to call a non-static method on a null reference`
- [ ] In daemon log expect: `POST /v1/adapters/connect HTTP/1.1 200 OK`

### 2. Verdict round-trip

- [ ] `&binit(rand(100000))` in Script Console → registers dummy bid.
- [ ] Type alias `mimikatz` in beacon console.
- [ ] In daemon log expect: `POST /v1/verdict/quick HTTP/1.1 200 OK`
- [ ] In beacon console expect: `[!] [Red Robin] DANGER: ...` (rule fires) OR `[*] [Red Robin] novel pattern — Layer B reviewing async`

### 3. Heartbeat

- [ ] Wait 30-40 seconds after .cna load.
- [ ] In daemon log expect: `GET /v1/adapter/heartbeat?adapter=cs-main HTTP/1.1 200 OK`

### 4. Actionable error logging

- [ ] Stop the daemon (`redrobin daemon stop`).
- [ ] Type any aliased command in the beacon console.
- [ ] In Script Console expect: `[Red Robin] daemon connection refused at http://localhost:7777 — is the daemon running?`
- [ ] Restart daemon. Next aliased command must succeed without warn.

### 5. Token sanitization

- [ ] Note the current `$REDROBIN_TOKEN` value (first 8 chars only — don't paste anywhere).
- [ ] Temporarily set `$REDROBIN_URL = "http://127.0.0.1:9999"` (no listener — produces a connect-refused error path).
- [ ] Type an aliased command.
- [ ] In Script Console: confirm the `warn` line does NOT contain the token's actual characters.
- [ ] Restore `$REDROBIN_URL` to its original value.

### 6. Handshake retry under daemon-boot race

- [ ] Stop daemon.
- [ ] Aggressor Scripts → Unload `redrobin.cna` → Load again.
- [ ] Within 1-2 seconds: `redrobin daemon start --host 127.0.0.1 --port 7777`
- [ ] In Script Console expect (depending on timing): `[Red Robin] handshake attempt 1/3 failed; retrying in 500ms` followed eventually by `[Red Robin] handshake OK; session=...`

### 7. Java version guard (optional smoke)

Skip if no easy way to fake Java version. Otherwise:

- [ ] Temporarily hard-code `$major = 8;` in `redrobin_check_java_version`.
- [ ] Reload .cna.
- [ ] In Script Console expect: `[Red Robin] FATAL: Java ... detected. Red Robin requires Java 11+.`
- [ ] Confirm `[Red Robin] handshake OK` does NOT appear (script aborted at throw).
- [ ] Revert the test edit.
````

- [ ] **Step 3: Commit**

```bash
git add adapters/cobaltstrike/tests/test_cna_smoke.md
git commit -m "docs(test-smoke): add CNA HTTP modernization verification steps

Maps the test plan from docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md
(section 9) into the existing smoke checklist format. Seven sub-steps
cover the new behavior: stock-launch handshake, verdict round-trip,
heartbeat, actionable error logging, token sanitization, handshake
retry, and Java version guard.

Useful for any future operator running through Tier 3 testing after
this refactor lands."
```

---

## Task 10: Update operator-setup docs

**Files:**
- Modify: `docs/operator-setup.md`

- [ ] **Step 1: Read the current operator setup doc**

Open `docs/operator-setup.md`. Search for any existing mention of `--add-opens`, `JVM flags`, `Java 8`, or `Java version`. There may be NONE — that's fine, we're adding the requirement explicitly.

- [ ] **Step 2: Find the Cobalt Strike adapter setup section**

Search for a section about loading the `.cna` script. Insert the new requirement note inside or near it. If no such section exists, add one at the end of the doc.

- [ ] **Step 3: Add the Java requirement note**

Add this block at the appropriate location:

```markdown
### Cobalt Strike Client Java requirement

Red Robin's Aggressor script (`redrobin.cna`) requires **Java 11+** on the CS
Client side. CS 4.7+ bundles Java 17+ in its standard distribution, so
operators on stock CS launches are automatically on a supported JVM.

**No JVM flags required.** Earlier Red Robin versions documented an
`--add-opens java.base/sun.net.www.protocol.http=ALL-UNNAMED` workaround
for Java module access issues. As of 2026-05-14 (the HTTP modernization
refactor — see `docs/superpowers/specs/2026-05-14-cna-http-modernization-design.md`),
the `.cna` uses the public `java.net.http.HttpClient` API and no longer
requires any `--add-opens` flag.

If the .cna fails to load with `Java ... detected. Red Robin requires
Java 11+` in the Script Console, upgrade your JVM (Temurin 21 or
Microsoft Build of OpenJDK 17 are both well-tested) and reload the
script.
```

- [ ] **Step 4: Commit**

```bash
git add docs/operator-setup.md
git commit -m "docs(operator-setup): note Java 11+ requirement for the .cna

Explicit operator-facing documentation that:
  - Red Robin's .cna requires Java 11+
  - Stock CS Client launches already meet this (CS 4.7+ bundles 17+)
  - The --add-opens workaround from earlier versions is no longer
    needed after the 2026-05-14 HTTP modernization refactor

Includes troubleshooting hint for what the operator sees if Java
< 11 (FATAL message in Script Console)."
```

---

## Task 11: Comprehensive end-to-end smoke validation

This is the gate task — running through every section of `test_cna_smoke.md` (specifically the new CNA HTTP modernization section added in Task 9) on a real CS Client + Red Robin daemon + teamserver.

**Files:** None modified. This is verification only.

- [ ] **Step 1: Ensure prerequisites**

- Red Robin daemon is running (`redrobin daemon start` from `$TEST_ROOT`).
- Cobalt Strike teamserver is running (`100.103.238.211` or wherever you've deployed it).
- CS Client is closed (we want a fresh launch to validate stock-launch behavior).

- [ ] **Step 2: Run the full smoke checklist**

Open `adapters/cobaltstrike/tests/test_cna_smoke.md`, navigate to the "CNA HTTP modernization smoke" section, and execute each of the 7 sub-steps in order. Check each `[ ]` box as you complete it.

- [ ] **Step 3: Capture any failures**

If any step fails, do NOT mark this task complete. Instead:

- Identify which task introduced the regression (use `git bisect` against tasks 1-10 if needed).
- Fix forward with a new commit (do NOT amend prior commits — they're already pushed).
- Re-run the failing step.

- [ ] **Step 4: Mark all task checkboxes complete + commit metadata**

When all 7 smoke sub-steps pass, no commit is needed (no file changes). The implementation is done.

Optional: open a PR / push to remote if the team's workflow requires.

```bash
# If the workflow requires push:
git push origin dev-juan
```

---

## Self-Review (skill checklist)

**Spec coverage:** Every section of the spec has at least one corresponding task:
- §5.1 Java version guard → Task 8
- §5.2 HttpClient singleton → Task 2 (global) + Task 4 (init)
- §5.3 Error sanitization → Task 3
- §5.4 POST helper → Task 5
- §5.5 GET helper + heartbeat refactor → Task 6
- §5.6 Handshake retry → Task 7
- §6 Behavior contracts → preserved in Tasks 5/6/7 (verify steps)
- §7 Java compatibility → enforced in Task 8
- §8 Security considerations → Task 3 + Task 5/6 verify steps
- §9 Test plan → Task 9 (smoke doc update) + Task 11 (run it)
- §10 Rollback plan → covered by per-task `git revert` simplicity
- §11 Implementation order → matches Tasks 1-8 order
- §12 Open questions → Q1 resolved during Task 5/6 implementation; Q2 resolved during Task 8 implementation; Q3 deferred to a v0.1.x cleanup ticket

**Placeholder scan:** No TBDs, no "implement later" markers, no "similar to Task N" — each task contains the complete code to write.

**Type consistency:**
- `$REDROBIN_HTTP_CLIENT` is declared in Task 2, initialized in Task 4, used in Tasks 5 + 6. ✓
- `redrobin_sanitize_error` defined in Task 3, called in Task 5. ✓
- `redrobin_http_post` rewritten in Task 5, called in Task 7's handshake. ✓
- `redrobin_http_get` defined in Task 6, called in Task 6's heartbeat. ✓
- `redrobin_check_java_version` defined in Task 8, called immediately after. ✓
- `redrobin_handshake_with_retry` defined in Task 7, called from boot block update in same task. ✓

All references consistent. Ready for execution.
