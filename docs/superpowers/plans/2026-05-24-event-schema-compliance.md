# Path-3 Event Schema Compliance Implementation Plan (closes #138)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `redrobin_push_event_async` produce schema-compliant `beacon.command_input` and `beacon.command_output` events so the daemon accepts them with HTTP 200 instead of rejecting with 400. After this lands, path-3 telemetry (operator input + beacon output flowing into working memory + episodic.db) actually works.

**Architecture:** Add a Java ULID generator (`RedRobinHttp.ulid()` — instance method to avoid class-loader-context issues on hook threads), use `java.time.Instant.now().toString()` for RFC3339 timestamps, switch from `on beacon_tasked` to `on beacon_input` (gains real `operator` + `raw` fields), and surface CS Aggressor's jobid (`$4` in BEACON_OUTPUT) as `task_id` for output events. JAR bumped to 1.0.2.

**Tech Stack:** Java 11+ (RedRobinHttp.java), JUnit 5 (ULID tests), Sleep/Aggressor (.cna), Crockford base32 (ULID encoding)

**Verified facts driving this plan (no assumptions, all reproduced via curl 2026-05-24):**

1. Schema rejects current envelope with: `'evt_<UUID>' does not match '^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$'` AND `'<Java Date.toString()>' does not match RFC3339 pattern`
2. Schema rejects beacon.command_input payload with: `'operator' is a required property`, `'command' is a required property`
3. Schema rejects beacon.command_output payload with: `'task_id' is a required property`
4. CS Aggressor `on beacon_input` signature: `$1=bid, $2=operator_nick, $3=command_line, $4=timestamp` (verified against vysecurity/AggressorScripts-1/All_In_One.cna and matched in `default.cna` patterns)
5. CS Aggressor `set BEACON_OUTPUT` signature: `$1=bid, $2=output, $3=timestamp, $4=jobid` (verified from `default.cna:805` comment `# beaconid, whatever: meh too, timestamp, jobid` + line 808 implementation `$jid = $4`)

**Risk note:** ULID generation has implementation pitfalls (alphabet collisions with I/L/O/U, monotonic ordering, sufficient entropy). The plan covers these with explicit TDD tests against the schema regex AND uniqueness across rapid invocations.

**Starting state assumption:** `dev-juan` HEAD = `d5872e0` (backlog updates) or later. JAR at version 1.0.1 in `adapters/cobaltstrike/jars/`. The #134 fix (`b7c7c69`) is applied — `redrobin_push_event_async` is synchronous.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` | Modify | Add public instance `ulid()` method (returns 26-char Crockford-b32 string) |
| `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java` | Modify | Add 5 JUnit cases pinning ULID contract |
| `adapters/cobaltstrike/helper-jar/build.ps1` | Modify | VERSION 1.0.1 → 1.0.2 |
| `adapters/cobaltstrike/helper-jar/build.sh` | Modify | Same |
| `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.2.jar` | Create | New build artifact |
| `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar` | Delete | Replaced |
| `adapters/cobaltstrike/redrobin.cna` | Modify | JAR ref bumps + emit subs refactor + hook signature changes |
| `docs/superpowers/backlog.md` | Modify (closing) | Mark #138 RESOLVED with commit refs |

---

## Task 1: TDD red — JUnit tests for `RedRobinHttp.ulid()` contract

**Files:**
- Modify: `adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java`

- [ ] **Step 1: Add 5 new test methods**

Append the following test methods to `RedRobinHttpTest.java` (preserve all existing tests):

```java
    // ULID contract tests (added 2026-05-24 for #138). The wire envelope
    // schema requires id to match `^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$`
    // (Crockford base32, 26 chars). These tests pin the ULID generator
    // against that exact requirement.

    @Test
    void ulid_isExactly26Chars() {
        RedRobinHttp http = new RedRobinHttp("http://localhost:7777", "token");
        String id = http.ulid();
        assertEquals(26, id.length(), "ULID must be exactly 26 chars");
    }

    @Test
    void ulid_usesCrockfordAlphabetOnly() {
        RedRobinHttp http = new RedRobinHttp("http://localhost:7777", "token");
        String id = http.ulid();
        // Crockford base32: 0-9 + A-Z minus I, L, O, U
        assertTrue(id.matches("^[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$"),
                "ULID must use only Crockford base32 alphabet (got '" + id + "')");
    }

    @Test
    void ulid_matchesEnvelopeSchemaPattern() {
        RedRobinHttp http = new RedRobinHttp("http://localhost:7777", "token");
        String id = "evt_" + http.ulid();
        // Exact regex from protocol/schemas/v0.1/envelope.schema.json
        assertTrue(id.matches("^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$"),
                "Prefixed ULID must match envelope schema regex");
    }

    @Test
    void ulid_consecutiveCallsAreDistinct() {
        RedRobinHttp http = new RedRobinHttp("http://localhost:7777", "token");
        String a = http.ulid();
        String b = http.ulid();
        assertNotEquals(a, b, "Two consecutive ULID calls must produce distinct IDs");
    }

    @Test
    void ulid_timestampPrefixIsMonotonic() throws InterruptedException {
        RedRobinHttp http = new RedRobinHttp("http://localhost:7777", "token");
        String early = http.ulid();
        Thread.sleep(2);  // ensure ms-clock advances at least one tick
        String late = http.ulid();
        // First 10 chars encode the 48-bit ms timestamp. Lexicographic
        // comparison in Crockford b32 preserves timestamp ordering.
        String earlyTs = early.substring(0, 10);
        String lateTs = late.substring(0, 10);
        assertTrue(lateTs.compareTo(earlyTs) >= 0,
                "Later ULID's ts prefix must compare >= earlier (earlyTs='"
                        + earlyTs + "', lateTs='" + lateTs + "')");
    }
```

- [ ] **Step 2: Verify the new tests FAIL to compile**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes
javac --release 11 -cp "lib/json-20240303.jar" -d build/classes $(find src -name '*.java')
javac --release 11 -cp "build/classes;lib/json-20240303.jar;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes $(find test -name '*.java') 2>&1 | tail -10
```

Expected: javac fails with `cannot find symbol: method ulid()`. The new tests reference `http.ulid()` which doesn't exist yet.

- [ ] **Step 3: Commit (RED state)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/helper-jar/test/com/redrobin/aggressor/RedRobinHttpTest.java
git commit -m "test(cna-jar): pin RedRobinHttp.ulid() contract (TDD red)

Five JUnit cases that fix the wire-envelope id format against the
v0.1 schema requirement (^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$):

  - exactly 26 chars
  - Crockford-b32 alphabet only (no I/L/O/U/lowercase/dashes)
  - prefixed form matches envelope schema regex verbatim
  - consecutive calls distinct (sufficient entropy in random tail)
  - timestamp prefix monotonic across calls (ms-precision)

Compile fails until Task 2 implements RedRobinHttp.ulid(). Closes
the part of #138 where the current id field ('evt_' + Java UUID with
dashes + lowercase) was rejected by the daemon's envelope validator
with: 'evt_<UUID>' does not match '^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$'."
```

---

## Task 2: TDD green — implement `RedRobinHttp.ulid()`

**Files:**
- Modify: `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java`

ULID spec: 48-bit timestamp (ms since epoch) + 80-bit randomness, encoded as 26 chars Crockford base32. Reference: https://github.com/ulid/spec.

- [ ] **Step 1: Add the ULID generator**

Open `RedRobinHttp.java`. After the constructor + `post()` + `get()` methods, before the inner `Response`/`HttpException` classes, add:

```java
    // ULID generator — used to produce wire envelope `id` values that
    // match the v0.1 schema regex ^evt_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$.
    // 48-bit ms timestamp prefix + 80-bit random suffix, Crockford-b32
    // encoded. Spec: https://github.com/ulid/spec.

    private static final char[] CROCKFORD_B32 = {
        '0','1','2','3','4','5','6','7','8','9',
        'A','B','C','D','E','F','G','H','J','K',
        'M','N','P','Q','R','S','T','V','W','X','Y','Z'
    };

    private static final java.security.SecureRandom ULID_RNG = new java.security.SecureRandom();

    public String ulid() {
        long ts = System.currentTimeMillis();
        byte[] randomness = new byte[10];  // 80 bits
        ULID_RNG.nextBytes(randomness);

        char[] out = new char[26];
        // 10 chars for the 48-bit timestamp prefix (big-endian)
        for (int i = 9; i >= 0; i--) {
            out[i] = CROCKFORD_B32[(int) (ts & 0x1f)];
            ts >>>= 5;
        }
        // 16 chars for the 80-bit random tail
        int bitBuf = 0;
        int bitsHeld = 0;
        int outIdx = 10;
        for (byte b : randomness) {
            bitBuf = (bitBuf << 8) | (b & 0xff);
            bitsHeld += 8;
            while (bitsHeld >= 5) {
                bitsHeld -= 5;
                out[outIdx++] = CROCKFORD_B32[(bitBuf >>> bitsHeld) & 0x1f];
            }
        }
        // 80 bits / 5 bits-per-char = 16 chars exactly, no leftover bits
        // 10 (ts) + 16 (random) = 26 chars
        return new String(out);
    }
```

Notes for the implementer:
- The Crockford alphabet excludes `I, L, O, U` (per spec) — verify the alphabet array above contains exactly 32 chars, no I/L/O/U.
- The 48-bit timestamp uses 50 bits of encoding capacity (10 chars × 5 bits) — that's fine for at least ~10889 years.
- 80 bits / 5 bits-per-char = 16 chars exactly — no bit padding needed.

- [ ] **Step 2: Compile + run all RedRobinHttpTest cases**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes
javac --release 11 -cp "lib/json-20240303.jar" -d build/classes $(find src -name '*.java')
javac --release 11 -cp "build/classes;lib/json-20240303.jar;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes $(find test -name '*.java')
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;lib/json-20240303.jar;build/test-classes" --scan-class-path 2>&1 | tail -15
```

Expected: 10 tests successful (the previous 5 RedRobinHttpTest cases + the 5 new ULID tests added in Task 1). 0 failed.

(The existing helper-jar suite total includes RedRobinEventStreamTest's 4 cases — those run too — so the FULL JUnit run will show 14 successful overall. RedRobinHttpTest alone is 10/10 after this commit.)

If any ULID test fails (e.g., a generated id contains `I` or `L` because the alphabet array had a typo), debug before committing.

- [ ] **Step 3: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java
git commit -m "feat(cna-jar): add RedRobinHttp.ulid() — schema-compliant id generator

ULID per github.com/ulid/spec: 48-bit ms timestamp + 80-bit random,
encoded as 26 chars Crockford base32 (excludes I/L/O/U). Pure JDK —
no new deps; uses java.security.SecureRandom + System.currentTimeMillis.

Instance method (not static) so the .cna can call it via the existing
\$REDROBIN_HTTP global without resolving the URLClassLoader-loaded
class from Aggressor hook contexts.

All 5 RedRobinHttpTest.ulid_* cases now pass (14/14 total). Closes the
envelope-id part of #138; ts + event payload fields land in subsequent
commits."
```

---

## Task 3: Bump build scripts to JAR version 1.0.2

**Files:**
- Modify: `adapters/cobaltstrike/helper-jar/build.ps1`
- Modify: `adapters/cobaltstrike/helper-jar/build.sh`

- [ ] **Step 1: Update `build.ps1`**

Find:
```powershell
$VERSION = "1.0.1"
```

Replace with:
```powershell
$VERSION = "1.0.2"
```

- [ ] **Step 2: Update `build.sh`**

Find:
```bash
VERSION="1.0.1"
```

Replace with:
```bash
VERSION="1.0.2"
```

- [ ] **Step 3: Confirm both files are symmetric**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "VERSION" adapters/cobaltstrike/helper-jar/build.ps1 adapters/cobaltstrike/helper-jar/build.sh
```

Both should now show `1.0.2`.

- [ ] **Step 4: Commit**

```bash
git add adapters/cobaltstrike/helper-jar/build.ps1 adapters/cobaltstrike/helper-jar/build.sh
git commit -m "build(cna-jar): bump version to 1.0.2 for #138 ULID

Minor bump — added the public ulid() instance method on RedRobinHttp
(no breaking change to existing API). 1.0.1 is rolled forward; the
.cna ref bump from 1.0.1 to 1.0.2 happens in the atomic .cna pivot
two commits later."
```

---

## Task 4: Build + ship 1.0.2 JAR, delete 1.0.1

**Files:**
- Create: `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.2.jar`
- Delete: `adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar`

- [ ] **Step 1: Build + copy 1.0.2 jar via inline commands** (matches Task 10 of the prior plan)

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/helper-jar
rm -rf build
mkdir -p build/classes build/test-classes build/jar
javac --release 11 -cp "lib/json-20240303.jar" -d build/classes $(find src -name '*.java')
javac --release 11 -cp "build/classes;lib/json-20240303.jar;lib/junit-platform-console-standalone-1.10.0.jar" -d build/test-classes $(find test -name '*.java')
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;lib/json-20240303.jar;build/test-classes" --scan-class-path
jar --create --file "build/jar/redrobin-aggressor-http-1.0.2.jar" -C build/classes .
cp build/jar/redrobin-aggressor-http-1.0.2.jar ../jars/
ls -la ../jars/
```

Expected: `redrobin-aggressor-http-1.0.2.jar` (~10-11 KB; slightly larger than 1.0.1 due to the new ulid method + alphabet array) + `json-20240303.jar` (unchanged) both in `../jars/`. 14/14 JUnit tests pass.

- [ ] **Step 2: Verify the new JAR contains the expected classes**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
jar --list --file adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.2.jar | sort
```

Expected entries (same as 1.0.1 — we only ADDED a method, no new classes):
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

- [ ] **Step 3: Delete the old 1.0.1 JAR**

```bash
git rm adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.1.jar
```

- [ ] **Step 4: Commit**

```bash
git add -f adapters/cobaltstrike/jars/redrobin-aggressor-http-1.0.2.jar
git commit -m "build(cna-jar): ship 1.0.2 artifact (drops 1.0.1)

Single binary delta — adds RedRobinHttp.ulid() instance method per the
TDD work in the prior two commits. All 6 class files present, all 14
JUnit cases pass clean. json-20240303.jar unchanged.

.cna still references 1.0.1 until the next commit; HEAD at this
commit cannot load redrobin.cna. Atomic .cna pivot in the next
commit closes that gap."
```

---

## Task 5: `.cna` — refactor `redrobin_push_event_async` + hook signatures (ATOMIC)

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna`

This task makes ALL the coordinated .cna changes in ONE atomic commit. Splitting risks leaving the .cna in a half-valid state.

### Step 1: Locate the current code blocks

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "redrobin-aggressor-http-1\|sub redrobin_push_event_async\|set BEACON_OUTPUT\|on beacon_tasked" adapters/cobaltstrike/redrobin.cna
```

Note line numbers for: both JAR refs (~lines 285 in init_http and ~lines 305 in init_event_stream), the push_event_async sub (~line 222), BEACON_OUTPUT filter (~line 333), beacon_tasked hook (~line 339).

### Step 2: Bump JAR refs from 1.0.1 to 1.0.2 (two places)

Find both occurrences of:
```sleep
    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.1.jar';
```

Replace each with:
```sleep
    $jar_path = script_resource() . '/jars/redrobin-aggressor-http-1.0.2.jar';
```

(One occurrence is in `redrobin_init_http`, the other in `redrobin_init_event_stream`.)

### Step 3: Replace `redrobin_push_event_async` with two specialized emit subs

Find the current `redrobin_push_event_async` sub (around lines 222-249):

```sleep
sub redrobin_push_event_async {
    global('$REDROBIN_SESSION_ID');
    local('$type $bid $payload $body');
    ($type, $bid, $payload) = @_;
    $body = '{"spec_version":"0.1.0","id":"evt_' . [java.util.UUID randomUUID] . '","ts":"' . dstamp(ticks()) . '"' .
            ',"source":{"adapter":"cs-aggressor","version":"1.0.0","engagement_id":"unknown"' .
            iff(strlen($REDROBIN_SESSION_ID) > 0, ',"adapter_session_id":"' . $REDROBIN_SESSION_ID . '"', "") . '}' .
            ',"event":{"type":"' . $type . '","bid":"' . redrobin_escape_json($bid) . '","' .
            iff($type eq "beacon.command_output", "output", "raw") . '":"' . redrobin_escape_json($payload) . '"}' .
            '}';
    try {
        redrobin_http_post("/v1/events", $body);
    } catch $e {
        # Swallow silently — daemon outage already surfaces via
        # redrobin_http_post's "[Red Robin] daemon connection refused" warn
        # block. Reraising here would suppress operator beacon output.
    }
}
```

Replace with:

```sleep
# ===== Envelope helper (private, called by emit subs below) =====
# Builds the v0.1 wire envelope shell around a pre-built event JSON. The
# event-body construction lives in the per-type emit subs because each
# event type has different required fields (verified 2026-05-24 against
# protocol/schemas/v0.1/events/*.schema.json + curl probes returning
# explicit envelope_errors / event_errors).
sub redrobin_envelope {
    global('$REDROBIN_HTTP $REDROBIN_SESSION_ID');
    local('$event_json');
    $event_json = $1;
    return '{"spec_version":"0.1.0",' .
           '"id":"evt_' . [$REDROBIN_HTTP ulid] . '",' .
           '"ts":"' . [[java.time.Instant now] toString] . '",' .
           '"source":{"adapter":"cs-aggressor","version":"1.0.0","engagement_id":"unknown"' .
           iff(strlen($REDROBIN_SESSION_ID) > 0, ',"adapter_session_id":"' . $REDROBIN_SESSION_ID . '"', "") . '},' .
           '"event":' . $event_json .
           '}';
}

# ===== Event emit subs (one per schema type) =====
# Each emit sub is synchronous (per #134 fix b7c7c69 — no internal fork()
# since the fork's isolated scope lost access to $REDROBIN_HTTP). Each
# wraps its HTTP POST in try/catch so daemon outages or 4xx never bubble
# out to suppress operator beacon output via Sleep filter semantics.
#
# Schema sources verified by curl 2026-05-24:
#   beacon.command_input  requires {type, bid, operator, command, raw}
#   beacon.command_output requires {type, bid, task_id, output}

sub redrobin_emit_command_input {
    local('$bid $operator $command $raw $event_json $body');
    ($bid, $operator, $command, $raw) = @_;
    $event_json = '{"type":"beacon.command_input",' .
                  '"bid":"' . redrobin_escape_json($bid) . '",' .
                  '"operator":"' . redrobin_escape_json($operator) . '",' .
                  '"command":"' . redrobin_escape_json($command) . '",' .
                  '"raw":"' . redrobin_escape_json($raw) . '"}';
    $body = redrobin_envelope($event_json);
    try {
        redrobin_http_post("/v1/events", $body);
    } catch $e {
        # Swallow silently — see #134 commit b7c7c69 rationale.
    }
}

sub redrobin_emit_command_output {
    local('$bid $task_id $output $event_json $body');
    ($bid, $task_id, $output) = @_;
    $event_json = '{"type":"beacon.command_output",' .
                  '"bid":"' . redrobin_escape_json($bid) . '",' .
                  '"task_id":"' . redrobin_escape_json($task_id) . '",' .
                  '"output":"' . redrobin_escape_json($output) . '"}';
    $body = redrobin_envelope($event_json);
    try {
        redrobin_http_post("/v1/events", $body);
    } catch $e {
        # Swallow silently — see #134 commit b7c7c69 rationale.
    }
}
```

### Step 4: Update `on beacon_tasked` → `on beacon_input` for command_input event

Find:

```sleep
# Tasking observation event — fires when a task is encoded for the beacon
on beacon_tasked {
    redrobin_push_event_async("beacon.command_input", $1, $2);
}
```

Replace with:

```sleep
# Operator-input observation event (Aggressor `on beacon_input`):
#   $1 = beacon ID
#   $2 = operator nickname (verified vysecurity/AggressorScripts-1)
#   $3 = command line typed by operator (raw)
#   $4 = timestamp (unused)
# Replaces an earlier `on beacon_tasked` handler that gave us only
# ($1=bid, $2=task_summary) — missing the operator + command fields
# required by the v0.1 schema for beacon.command_input. See #138.
on beacon_input {
    local('@parts $command');
    @parts = split(' ', $3);
    $command = iff(size(@parts) > 0, @parts[0], "");
    redrobin_emit_command_input($1, $2, $command, $3);
}
```

### Step 5: Update `set BEACON_OUTPUT` to surface task_id ($4)

Find:

```sleep
# ===== Path 3 — Output observation (passthrough-safe filter hook) =====
set BEACON_OUTPUT {
    redrobin_push_event_async("beacon.command_output", $1, $2);
    return $2;
}
```

Replace with:

```sleep
# ===== Path 3 — Output observation (passthrough-safe filter hook) =====
# Aggressor BEACON_OUTPUT signature ($1=bid, $2=output, $3=ts, $4=jobid):
#   default.cna line 805 comment: "beaconid, whatever: meh too, timestamp, jobid"
#   default.cna line 808 implementation: `$jid = $4`
# We surface $4 as task_id; if CS didn't assign one (e.g., for output
# without a backing job), default to "" — the schema requires the field
# but allows any string value.
set BEACON_OUTPUT {
    local('$task_id');
    $task_id = iff($4 !is $null, $4, "");
    redrobin_emit_command_output($1, $task_id, $2);
    return $2;
}
```

### Step 6: Verify all three changes landed atomically

```bash
grep -n "1\.0\.1\.jar\|1\.0\.2\.jar\|redrobin_push_event_async\|redrobin_emit_command_\|redrobin_envelope\|on beacon_tasked\|on beacon_input\|set BEACON_OUTPUT" adapters/cobaltstrike/redrobin.cna
```

Expected:
- TWO occurrences of `redrobin-aggressor-http-1.0.2.jar` (both init subs)
- ZERO occurrences of `1.0.1.jar`
- ZERO occurrences of `redrobin_push_event_async` (the old sub is gone)
- One occurrence each of `sub redrobin_envelope`, `sub redrobin_emit_command_input`, `sub redrobin_emit_command_output`
- ONE occurrence of `on beacon_input {` (no longer `on beacon_tasked`)
- One occurrence of `set BEACON_OUTPUT {` with `$task_id = iff(...)` body

```bash
grep -n "1\.0\.0\.jar\|1\.0\.1\.jar" adapters/cobaltstrike/redrobin.cna
```

Expected: empty.

### Step 7: Commit (atomic .cna pivot)

```bash
git add adapters/cobaltstrike/redrobin.cna
git commit -m "feat(cna): schema-compliant path-3 events (closes #138)

Atomic .cna refactor — six coordinated changes that turn the post-#134
synchronous POSTs from 400 Bad Request into 200 OK. Verified against
protocol/schemas/v0.1/{envelope.schema.json, events/*.schema.json}
via curl probes captured in backlog.md #138.

  1. JAR ref bumped 1.0.1 -> 1.0.2 in both init_http and
     init_event_stream (the new RedRobinHttp.ulid() instance method
     lives in 1.0.2).
  2. New sub redrobin_envelope(\$event_json): builds the wire envelope
     shell with id=[evt_ + \$REDROBIN_HTTP ulid] and ts=
     [[java.time.Instant now] toString] (RFC3339). Replaces the old
     evt_ + [java.util.UUID randomUUID] + dstamp(ticks()) which the
     envelope schema rejected for failing both id and ts patterns.
  3. New sub redrobin_emit_command_input(\$bid, \$operator, \$command,
     \$raw): builds and POSTs a beacon.command_input event with all
     four required schema fields (was missing operator + command).
  4. New sub redrobin_emit_command_output(\$bid, \$task_id, \$output):
     builds and POSTs a beacon.command_output event with task_id
     (was missing) sourced from CS Aggressor's \$4 in BEACON_OUTPUT
     (verified from default.cna line 805 comment + line 808 \$jid=\$4).
  5. Hook switch on beacon_tasked -> on beacon_input. The new
     signature \$1=bid, \$2=operator_nick, \$3=raw, \$4=ts (verified
     against vysecurity/AggressorScripts-1/All_In_One.cna) gives us
     the operator and raw fields the schema needs. command is the
     first whitespace-separated token of \$3.
  6. set BEACON_OUTPUT now passes \$4 (jobid) as task_id, falling
     back to '' when null (some CS output paths lack a backing
     job ID; schema requires the field but accepts empty string).

The old redrobin_push_event_async sub is deleted — the two new emit
subs replace it with type-specific signatures so each event type's
required fields are encoded in the function signature instead of an
opaque \$payload arg + conditional 'output'/'raw' branch.

After this commit redrobin.cna is loadable again (was broken in the
prior commit while jars/ shipped 1.0.2 but .cna still referenced 1.0.1).
Path-3 telemetry (operator command input + beacon output observation)
now actually persists to working_memory + episodic.db for the active
engagement instead of being rejected with HTTP 400."
```

---

## Task 6: Smoke validation (operator-run)

**Files:** None modified. Verification only.

- [ ] **Step 1: Restart daemon, reload .cna**

```powershell
# In daemon terminal: Ctrl+C, then restart.
redrobin daemon start --host 127.0.0.1 --port 7777
# Banner should show: Engagement: tc4-smoke
```

In CS Client: Script Manager → unload `redrobin.cna` → load `redrobin.cna`.

Expected in Script Console:
- `[Red Robin] handshake OK; session=ses_<uuid>`
- `[Red Robin] .cna loaded; gate active.`
- NO `[!] Attempted to call a non-static method on a null reference` errors
- NO `[Red Robin] daemon returned non-2xx for /v1/events` warn lines

- [ ] **Step 2: Trigger a non-aliased shell command**

```
beacon> shell whoami
```

Expected in daemon log:
- `POST /v1/verdict/quick HTTP/1.1" 200 OK` (Layer A gate)
- `POST /v1/events HTTP/1.1" 200 OK` (beacon.command_input from on beacon_input)
- `POST /v1/events HTTP/1.1" 200 OK` (beacon.command_output from BEACON_OUTPUT, when beacon returns)

**Zero 400s.** If any 400 appears, capture the response body via curl as in Task 5 verification.

Expected in beacon console:
- `[Red Robin] novel pattern — Layer B reviewing async`
- `[*] Tasked beacon to run: whoami`
- `[+] host called home, sent: NN bytes` (when beacon checks in)
- `received output:` + `jcsp\jcspi` (or operator's actual user)
- `[Red Robin] Layer B WARN: ...`

- [ ] **Step 3: Verify path-3 events actually reached working memory + episodic.db**

```powershell
# Working memory:
python -c "import json; print(json.dumps(json.load(open(r'C:\Users\jcspi\.redrobin\engagements\tc4-smoke\working_memory.json')), indent=2)[:2000])"

# Episodic — input + output events should now also appear (alongside the verdict rows from before):
python -c "import sqlite3,sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect(r'C:\Users\jcspi\.redrobin\episodic.db'); rows=c.execute(\"SELECT id,command,verdict,substr(reason,1,80),created_at,engagement_id FROM episodes WHERE engagement_id='tc4-smoke' ORDER BY id DESC LIMIT 10\").fetchall(); [print(r) for r in rows]"
```

(Note: episodic.db lives at the ROOT per the verified architecture; per-engagement DB at `engagements/<slug>/episodic.db` is the B2-isolated location. Tc4-smoke rows live in the per-engagement DB.)

Per-engagement DB:
```powershell
python -c "import sqlite3,sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect(r'C:\Users\jcspi\.redrobin\engagements\tc4-smoke\episodic.db'); rows=c.execute('SELECT id,command,verdict,substr(reason,1,80),created_at FROM episodes ORDER BY id DESC LIMIT 10').fetchall(); [print(r) for r in rows]"
```

Expected: rows for `shell whoami` Layer A ESCALATE + Layer B WARN already present (from prior session). Plus working_memory.json showing any sessions/hosts/credentials the reducer extracted from beacon.command_output events.

- [ ] **Step 4: If everything green, close #138 in the backlog**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
# Edit docs/superpowers/backlog.md — change #138 status to RESOLVED with commit refs
git add docs/superpowers/backlog.md
git commit -m "docs(backlog): close #138 — path-3 schema compliance shipped

#138 resolved by commits [T1 SHA]..[T5 SHA]. Five tasks:
  T1 (red): ULID JUnit tests
  T2 (green): RedRobinHttp.ulid() impl
  T3: build script version bump
  T4: ship 1.0.2 jar, drop 1.0.1
  T5: atomic .cna refactor (envelope ts+id, new emit subs, hook
      switches for operator/command/task_id)
  T6 (this commit): smoke validate + close.

All 4 schema mismatches verified resolved by curl + smoke:
  envelope.id   UUID -> ULID (matches ^evt_[Crockford-b32]{26}\$)
  envelope.ts   Date.toString -> Instant.toString (RFC3339)
  command_input adds operator + command (from on beacon_input \$2/\$3)
  command_output adds task_id (from BEACON_OUTPUT \$4)

POST /v1/events now returns 200; path-3 telemetry (operator input +
beacon output) lands in working_memory + episodic for the active
engagement."
```

---

## Self-Review

**Spec coverage:** Every one of the four verified schema mismatches from backlog.md #138 has a task:
- envelope.id (UUID format) → Tasks 1+2 (ULID generator + tests)
- envelope.ts (Java Date.toString) → Task 5 (Instant.toString in redrobin_envelope)
- command_input.operator + .command → Task 5 (new emit sub + on beacon_input hook switch)
- command_output.task_id → Task 5 (BEACON_OUTPUT $4 surfaced)

**Placeholder scan:** No TBD/TODO/"implement later". Every step has the exact code or command to run.

**Type / name consistency:**
- `RedRobinHttp.ulid()` defined in Task 2 → called as `[$REDROBIN_HTTP ulid]` in Task 5's `redrobin_envelope`. ✓
- `redrobin_envelope($event_json)` defined in Task 5 → called by both `redrobin_emit_command_input` and `redrobin_emit_command_output` in the same task. ✓
- `redrobin_emit_command_input($bid, $operator, $command, $raw)` defined in Task 5 → called from `on beacon_input` in same task. ✓
- `redrobin_emit_command_output($bid, $task_id, $output)` defined in Task 5 → called from `set BEACON_OUTPUT` in same task. ✓
- 1.0.2 jar version: VERSION="1.0.2" in build scripts (Task 3), `redrobin-aggressor-http-1.0.2.jar` in jars/ (Task 4), referenced from .cna in two places (Task 5). ✓

All references consistent. Ready for execution.
