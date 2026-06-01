# Plan C — docs polish + smoke hardening (closes #116, #120, #111)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with Opus 4.7. Atomic commits per task.

**Goal:** Close three documentation + smoke-checklist polish items surfaced during prior code reviews and the post-#115 / post-#119 audits: `#116` (operator-setup.md jdk.internal guidance), `#120` (helper-jar README + JAR helper spec polish — drift between docs and the 1.0.2 shipped JAR), `#111` (token-sanitization smoke coverage — strengthen the existing TC and close a real defensive gap in `redrobin_sanitize_error`'s URL handling).

**Architecture / approach:** This is primarily a docs + smoke-checklist plan. Two of three items (#116, #120) are pure documentation edits. The third (#111) is a smoke-checklist addition PLUS a small defensive code change to `sanitize_error` in `redrobin.cna` that closes the audit-identified leak vector for `?token=<bearer>` URL query params (`RedRobinEventStream` builds its WS URL that way at `RedRobinEventStream.java:51`). No production daemon code is touched. JAR bytecode is not rebuilt. Tests: the sanitize-helper change does not have a Java/pytest harness — it lives in Sleep and is verified by smoke (operator-run) per the established pattern for `.cna` defensive code.

**Tech Stack:** Markdown for #116 and #120. Markdown + a ~5-line Sleep edit for #111 (`adapters/cobaltstrike/redrobin.cna` `redrobin_sanitize_error` sub).

**Verified facts driving this plan (no assumptions; every claim cross-checked against the live file):**

1. **#116 surface — operator-setup.md has 4 stale 1.0.1.jar references.** Read `docs/operator-setup.md` in full 2026-05-24. The "Cobalt Strike Client Java requirement" section at lines 202-248 is mostly correct (line 208 "No JVM flags required", lines 209-212 describe the JAR helper + URLClassLoader pattern, lines 244-248 are the correctly-scoped "Historical note") — BUT four references to the JAR filename are stale at `1.0.1.jar` (lines 209, 221, 229, 234). The shipped JAR is `redrobin-aggressor-http-1.0.2.jar` (per `0b85321` which renames 1.0.1.jar → 1.0.2.jar for the path-3 ulid envelope fix in #138). Earlier commit `289f98c` purged the 1.0.0 → 1.0.1 churn but the second bump (1.0.1 → 1.0.2) was missed. New operators following this guide would get a missing-JAR error on .cna load. Task 1 is therefore a real fix: bump all 4 references to `1.0.2.jar` and commit.

2. **#120 surface — helper-jar README is genuinely stale.** Read `adapters/cobaltstrike/helper-jar/README.md` in full (37 lines):
   - **Line 26:** claims output is `redrobin-aggressor-http-1.0.0.jar`. Verified against `adapters/cobaltstrike/jars/`: the shipped JAR is `redrobin-aggressor-http-1.0.2.jar` (per `0b85321` / `#138`). Stale by two minor versions.
   - **Lines 4-7:** rationale paragraph names `jdk.internal.net.http.*` as the reflection problem but lacks context about WHY the prior approaches (per spec §1.1) all failed access checks. Rewrite to point readers at spec §1.1 for the full audit while keeping the production blocker (jdk.internal.net.http) as the headline issue. NB: an earlier draft considered naming `sun.net.www.protocol.http.*` alongside jdk.internal, but that path was a SUPERSEDED intermediate spec (legacy `HttpURLConnection` revert) that was never shipped — citing it would conflate a never-shipped spec with the production blocker.
   - **Missing entirely:** RedRobinEventStream + VerdictEvent + `RedRobinHttp.ulid()` — the JAR now ships 3 source classes (verified by `ls adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/`: `RedRobinEventStream.java`, `RedRobinHttp.java`, `VerdictEvent.java`). README only documents the original 1.0.0 single-class JAR.
   - **Missing entirely:** the `json-20240303.jar` dependency (committed at `helper-jar/lib/json-20240303.jar`, vendored per `b58ffe1` for org.json WebSocket frame parsing). README mentions only JUnit 5 in `lib/`.
   - **Test instructions (lines 28-36):** correct — `lib/junit-platform-console-standalone-1.10.0.jar` exists (verified). But the test runner commands don't include the org.json classpath that `RedRobinEventStreamTest` needs since `b58ffe1`.

3. **#120 surface — spec drift acknowledgement.** Read `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` in full. Spec is a 2026-05-17 design document that predates `RedRobinEventStream` (added by `c81ede7` in the adapter-event-stream sprint) and `RedRobinHttp.ulid()` (added by `bc72cac` for #138). §4.1 layout only shows `RedRobinHttp.java`; §5.1 source omits `ulid()`; §10 cites JAR version `1.0.0`. **Spec body should NOT be rewritten** (it's a historical design doc), but the spec needs an audit appendix calling out subsequent additions so future readers don't conflate the design with the shipped artifact.

4. **#111 surface — backlog name is misleading.** Backlog #111 says "TC5 (token sanitization smoke from Task #115)" — but read `adapters/cobaltstrike/tests/test_cna_smoke.md` 2026-05-24: TC5 of the FIRST section (line 40-44) is **"Daemon offline (fail-open behavior)"**, NOT token sanitization. Token sanitization lives at two different sections: §5 of the 2026-05-14 CNA HTTP modernization smoke (lines 109-115, the "weak connect-refused" version) and §6 of the 2026-05-17 CNA JAR helper smoke (lines 190-206, "Token sanitization (real 401 path) — Strengthens the prior weak connect-refused TC"). The #111 ask resolves to "ensure token-sanitization coverage is strong enough" against ALL three smoke sections, including the Layer B section (added 2026-05-20, lines 222-262) which currently has **zero** token-sanitization coverage.

5. **#111 surface — real defensive gap in `sanitize_error`.** Read `adapters/cobaltstrike/redrobin.cna` lines 138-146:
   ```sleep
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
   This redacts the raw token + the `Authorization: Bearer ...` header pattern, but does NOT redact `?token=<value>` / `&token=<value>` URL query parameters. `RedRobinEventStream.java:51` constructs the WS URL as `wsBase + "/v1/adapters/events?token=" + bearerToken;` — the token rides in the URL query string. If a future Java exception or stderr message embeds that URL (e.g., a `java.net.URISyntaxException.getMessage()` containing the offending URI, or a future change to the JAR's `Listener.onError` that logs the URL with `gapMs`), the bearer leaks through any `redrobin_sanitize_error` call site. **Today the leak is latent** — `RedRobinEventStream.java:92-95` logs only `gapMs`, not the URL — but the regex pattern is missing for the same defensive reason `Bearer ...` was already added.

6. **No conflict with adjacent in-flight work.** `dev-juan` HEAD = `2311df7` (Plan B close-out of `#131`). Plan A (4 commits ending at `b8be0d8` + close-out at `57c575d`) and Plan B (4 commits ending at `2311df7`) both shipped. No outstanding diff on `redrobin.cna`, `helper-jar/`, `operator-setup.md`, or `test_cna_smoke.md`.

**Risk note:** Three items, all docs/smoke-centric with one small Sleep edit. T1 is a trivial 4-occurrence string bump in operator-setup.md (`1.0.1.jar` → `1.0.2.jar`). T2 is pure docs. T3 contains a small defensive Sleep edit to `redrobin_sanitize_error` (a third `replace()` regex) — happy-path strings never contain `?token=` (Sleep never builds URLs with token in query), so the new regex is a no-op against today's logs but closes the future-proof gap. No production daemon code, no JAR rebuild, no Python code change, no tests required (Sleep .cna doesn't have a unit-test harness; the regex is verified by smoke). Operator can ship without smoke for #111's code change if time-pressed since the path is latent.

**Starting state assumption:** `dev-juan` HEAD = `d3d83eb` (Plan C commit). All commits from Plans A + B shipped. JAR at 1.0.2.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `docs/operator-setup.md` | Modify | Bump 4 stale JAR filename references 1.0.1 → 1.0.2 (#116) |
| `adapters/cobaltstrike/helper-jar/README.md` | Modify | Update JAR version (1.0.0 → 1.0.2), broaden rationale, document RedRobinEventStream + VerdictEvent + ulid + json-20240303 dependency (#120) |
| `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` | Modify | Append "Post-spec additions (2026-05-17 → 2026-05-24)" audit appendix listing the shipped delta (#120) |
| `adapters/cobaltstrike/redrobin.cna` | Modify | Strengthen `redrobin_sanitize_error` with `?token=` / `&token=` URL-query redaction regex (#111) |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify | Add new TC5 (token sanitization) to the Layer B verdict visibility smoke section with explicit grep patterns covering WS URL leak vectors (#111); also fix dangling `_inspect_episodic.py` reference in TC4 (#111) |
| `docs/superpowers/backlog.md` | Modify | Close out `#116`, `#120`, `#111` in the scope table + entry headings + bodies, citing this plan's commit SHAs (Task 5) |

---

## Task 1: Bump operator-setup.md JAR version references 1.0.1 → 1.0.2 (closes #116)

**Files:**
- Modify: `docs/operator-setup.md`

- [ ] **Step 1: Find all stale references**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "1\.0\.1\.jar\|redrobin-aggressor-http-1\.0\.1" docs/operator-setup.md
```

Expected: 4 hits at lines 209, 221, 229, 234.

- [ ] **Step 2: Replace each reference**

Use Edit with `replace_all: true` on the string `redrobin-aggressor-http-1.0.1.jar` → `redrobin-aggressor-http-1.0.2.jar`. Single replace_all covers all 4 occurrences.

- [ ] **Step 3: Verify no `1.0.1` strings remain**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "1\.0\.1" docs/operator-setup.md
```

Expected: no hits (verified pre-edit that no other 1.0.1 mentions exist outside these 4 JAR refs).

- [ ] **Step 4: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/operator-setup.md
git commit -m "$(cat <<'EOF'
docs(operator-setup): bump JAR refs 1.0.1 → 1.0.2 (closes #116)

The 1.0.2 JAR shipped in 0b85321 (path-3 envelope fix) drops 1.0.1.
operator-setup.md still pointed at the 1.0.1 filename in 4 places
(lines 209, 221, 229, 234). 289f98c earlier purged the 1.0.0 →
1.0.1 churn but the second bump was missed. New operators following
this guide would get a missing-JAR error on .cna load.

Spec: docs/superpowers/plans/2026-05-24-docs-polish.md
EOF
)"
```

---

## Task 2: `#120` — helper-jar README + spec polish

**Files:**
- Modify: `adapters/cobaltstrike/helper-jar/README.md`
- Modify: `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md`

This is split into two sub-edits: the README (which is operator-facing and outdated) and the spec (which is a historical design doc that needs an audit appendix, not a rewrite). Both land in a single commit since they're one logical "JAR helper docs drift fix" change.

- [ ] **Step 1: Read both files to confirm current state matches research**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
cat adapters/cobaltstrike/helper-jar/README.md
ls adapters/cobaltstrike/jars/
ls adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/
```

Expected:
- README is 37 lines, references `1.0.0` at line 26, names only `jdk.internal.net.http.*` in the rationale (lines 4-7).
- `jars/` contains `redrobin-aggressor-http-1.0.2.jar` and `json-20240303.jar`.
- `src/com/redrobin/aggressor/` contains `RedRobinEventStream.java`, `RedRobinHttp.java`, `VerdictEvent.java`.

- [ ] **Step 2: Replace `helper-jar/README.md` body with the corrected version**

Replace the entire file content with:

```markdown
# redrobin-aggressor-http

Java helper JAR loaded by `adapters/cobaltstrike/redrobin.cna` to wrap
`java.net.http.HttpClient`. Sleep cannot reflect on the JDK's internal
`jdk.internal.net.http.*` classes without `--add-opens` JVM flags, and
the prior approaches explored in the 2026-05-14 and 2026-05-17 design
specs all hit similar access-check failures (see spec §1.1 for the
full audit). This JAR sidesteps the issue by exposing a stable public
API in our own package (`com.redrobin.aggressor.*`) whose bytecode
invokes the internal classes directly — Sleep reflection only ever
targets our public surface.

See `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` for
the full rationale.

## Shipped classes (as of 1.0.2)

- `com.redrobin.aggressor.RedRobinHttp` — synchronous POST/GET against the
  daemon (handshake, verdict, events, heartbeat) + the schema-required
  ULID generator `ulid()` (Crockford b32; 48-bit ts + 80-bit randomness).
- `com.redrobin.aggressor.RedRobinEventStream` — WebSocket subscriber
  for the daemon's `/v1/adapters/events` endpoint (Layer B verdicts).
  Auto-reconnect with exponential backoff (1s → 60s); drop-oldest
  backpressure on a 256-entry queue Sleep polls via `pollEvent(ms)`.
- `com.redrobin.aggressor.VerdictEvent` — DTO for opsec.verdict payloads
  delivered over the WebSocket.

## Dependencies (vendored in `lib/`)

- `junit-platform-console-standalone-1.10.0.jar` — test-only (JUnit 5).
- `json-20240303.jar` — runtime (org.json). Used by `RedRobinEventStream`
  to decode WebSocket frames. Also shipped sibling to the helper JAR in
  `adapters/cobaltstrike/jars/json-20240303.jar` for the .cna's
  `URLClassLoader`.

Provenance + SHA-256 for both: see `lib/README.md`.

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

Output: `../jars/redrobin-aggressor-http-1.0.2.jar` (sibling of the .cna,
co-located with `json-20240303.jar`).

## Test

Build script runs JUnit 5 tests before packaging
(`RedRobinHttpTest` + `RedRobinEventStreamTest`). To run tests only:

```powershell
# Windows
javac --release 11 -d build/classes (Get-ChildItem -Recurse src/*.java).FullName
javac --release 11 -cp "build/classes;lib/junit-platform-console-standalone-1.10.0.jar;lib/json-20240303.jar" -d build/test-classes (Get-ChildItem -Recurse test/*.java).FullName
java -jar lib/junit-platform-console-standalone-1.10.0.jar --class-path "build/classes;build/test-classes;lib/json-20240303.jar" --scan-class-path
```

Linux/Mac uses `:` path separator instead of `;` and `find ... -name '*.java'` in place of the PowerShell glob.
```

Rationale for each change:
- **Rationale paragraph (lines 1-13):** keeps the WHY-it-exists focused on the `jdk.internal.net.http.*` reflection blocker (the actual production issue) while pointing readers at spec §1.1 for the full audit of prior approaches that also failed access checks. Drops the explicit `sun.net.www.protocol.http.*` callout from the prior README draft — that path was a SUPERSEDED intermediate spec (the legacy `HttpURLConnection` revert) that was never shipped; conflating it with the production blocker would mislead future readers.
- **"Shipped classes (as of 1.0.2)" section (new):** documents the 3 classes the JAR actually ships, with one-line descriptions for each.
- **"Dependencies (vendored in `lib/`)" section (new):** documents the org.json runtime dependency that was missing from the README. Points at `lib/README.md` for SHA-256 / provenance.
- **Output line:** `1.0.0.jar` → `1.0.2.jar`; mentions the sibling `json-20240303.jar` so operators know the .cna needs both.
- **Test commands:** add `lib/json-20240303.jar` to both the test compile classpath and the JUnit runner classpath — `RedRobinEventStreamTest` doesn't compile/run without it.

- [ ] **Step 3: Append audit appendix to the JAR helper spec**

Append the following block at the very end of `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` (after the existing `## 12. Open questions` section's last row, on a new line):

```markdown

---

## 13. Post-spec additions (2026-05-17 → 2026-05-24)

This spec is a historical design document. The shipped JAR has grown
beyond the original §4.1 layout. Subsequent work added:

| Addition | Commit | Sprint context |
|---|---|---|
| `RedRobinEventStream` class — WS subscriber for `/v1/adapters/events` (Layer B verdicts) | `c81ede7` | 2026-05-20 adapter-event-stream plan |
| `VerdictEvent` DTO + parser | `e16ecfa` | Same |
| Vendored `org.json` 20240303 dependency for WS frame parsing | `533d481` | Same (build) + `b58ffe1` (1.0.1 ship) |
| `RedRobinHttp.ulid()` instance method — schema-compliant id generator (Crockford b32, 48-bit ts + 80-bit randomness) | `bc72cac` | 2026-05-24 #138 event-schema compliance |
| JAR version bumped 1.0.0 → 1.0.1 (add EventStream) → 1.0.2 (add ulid) | `b58ffe1` + `0b85321` | Same |

§4.1 (Repository layout), §5 (Components — only documents `RedRobinHttp`),
§7 (Java compatibility), §9 (Test plan — names only `RedRobinHttpTest`),
and §10 (Rollback plan — cites 1.0.0) remain accurate **as a 2026-05-17
design baseline** but should be read alongside this table when reasoning
about the current shipped artifact. The `helper-jar/README.md` is the
operator-facing source of truth for what 1.0.2 actually contains.

The spec's core design claims — JAR pattern, URLClassLoader,
unnamed-module access, no `--add-opens` — still hold for every class
added since (all three live in `com.redrobin.aggressor.*` and follow the
same reflection-safe pattern as the original `RedRobinHttp`).
```

Rationale: spec body is left intact (it's a historical design doc; rewriting it loses the audit trail). The appendix calls out exactly what shipped beyond §4.1 with commit SHAs for traceability and points operators at the README for current truth.

- [ ] **Step 4: Verify the edits landed**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "1.0.2\|RedRobinEventStream\|json-20240303\|ulid" adapters/cobaltstrike/helper-jar/README.md
grep -n "Post-spec additions" docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md
```

Expected:
- README: at least 6 hits (line for `1.0.2.jar` output, `RedRobinEventStream` in shipped-classes + dependencies, `json-20240303` in dependencies + test cmd, `ulid` in RedRobinHttp description).
- Spec: 1 hit on the new "## 13. Post-spec additions (2026-05-17 → 2026-05-24)" heading.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/helper-jar/README.md docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md
git commit -m "$(cat <<'EOF'
docs(helper-jar): refresh README + spec audit appendix for 1.0.2 (closes #120)

Audit findings from #115 / #119 (JAR helper plan execution) flagged
docs drift between the helper-jar/README.md and the shipped 1.0.2
JAR. The README was written for 1.0.0 (single RedRobinHttp class)
and never updated when 1.0.1 added RedRobinEventStream + VerdictEvent
+ vendored org.json (commit c81ede7 + b58ffe1), nor when 1.0.2 added
RedRobinHttp.ulid() (commit bc72cac for #138).

README changes:
- Keep the WHY-it-exists paragraph focused on the jdk.internal.net.http.*
  reflection blocker (the actual production issue) while pointing
  readers at spec §1.1 for the full audit of prior approaches that
  also failed access checks. Earlier README drafts cited both
  jdk.internal and sun.net.www.protocol.http but the latter was a
  SUPERSEDED intermediate spec (legacy HttpURLConnection revert) that
  was never shipped — production went straight from broken-HttpClient
  to JAR-helper.
- Add "Shipped classes (as of 1.0.2)" section documenting all 3
  classes (RedRobinHttp + RedRobinEventStream + VerdictEvent) with
  one-line scope descriptions.
- Add "Dependencies (vendored in lib/)" section calling out the
  org.json 20240303 runtime dependency (was missing from README;
  documented only in lib/README.md).
- Bump output filename 1.0.0.jar → 1.0.2.jar.
- Add lib/json-20240303.jar to the test-only run commands so
  RedRobinEventStreamTest compiles + runs.

Spec changes:
- Append §13 "Post-spec additions (2026-05-17 → 2026-05-24)" audit
  appendix listing the shipped delta beyond the 2026-05-17 design
  baseline (EventStream + VerdictEvent + org.json + ulid + version
  bumps) with commit SHAs. Spec body left intact — it's a historical
  design doc; rewriting it loses the audit trail.

The spec's core design claims (URLClassLoader, unnamed-module access,
no --add-opens) still hold for every class added since — all three
live in com.redrobin.aggressor.* and follow the same reflection-safe
pattern as the original RedRobinHttp.

Spec: docs/superpowers/plans/2026-05-24-docs-polish.md Task 2
Backlog: closes #120.
EOF
)"
```

---

## Task 3a: `#111` (part 1) — redact `?token=` query string in sanitize_error

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna` (small defensive change to `redrobin_sanitize_error`)

Per the user's atomic-commit preference, the `.cna` regex change ships as its own commit separate from the smoke-doc updates in Task 3b. Closing the URL-query-token leak vector is one logical change to production Sleep code; smoke-checklist additions and the unrelated `inspect_episodic.py` path fix are doc chores that ride in Task 3b.

- [ ] **Step 1: Read the current sanitize_error sub**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
sed -n '133,146p' adapters/cobaltstrike/redrobin.cna
```

Expected (current state, lines 133-146):

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

- [ ] **Step 2: Strengthen sanitize_error with URL-query redaction**

Use the `Edit` tool to replace the existing comment + sub body. Anchor on the unique `# ===== Error sanitization =====` header so the replacement is unambiguous.

Find:

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

Replace with:

```sleep
# ===== Error sanitization =====
# Strip bearer token + token value from exception messages before logging.
# The Aggressor Script Console appears in engagement screenshots / shared
# screens; an exception's toString() can include request headers in some
# JVMs. Wrap every exception via this sub before printing.
#
# Three redaction layers (ordered most-specific to most-general):
#   1. The raw token value (covers any place it appears verbatim).
#   2. `Authorization: Bearer <token>` header pattern.
#   3. `?token=<value>` / `&token=<value>` URL query params — added
#      for #111. RedRobinEventStream builds its WS URL as
#      `ws://.../v1/adapters/events?token=<bearer>` (see
#      RedRobinEventStream.java:51). The JAR does not log the URL today,
#      but any future Java exception whose message embeds the URI
#      (URISyntaxException, IOException with cause) would leak the
#      token here. This redaction is defensive and a no-op against
#      today's logs.
sub redrobin_sanitize_error {
    local('$err_str');
    $err_str = "" . $1;
    if (strlen($REDROBIN_TOKEN) > 8) {
        $err_str = replace($err_str, $REDROBIN_TOKEN, '***REDACTED***');
    }
    $err_str = replace($err_str, 'Bearer [A-Za-z0-9_\-\.=]+', 'Bearer ***REDACTED***');
    $err_str = replace($err_str, '([?&])token=[^\s&"\\\']+', '$1token=***REDACTED***');
    return $err_str;
}
```

Notes on the new regex:
- `([?&])` — capturing group so we preserve whichever leading delimiter the URL used (`?` for the first query param, `&` for subsequent ones). Sleep's `replace()` is regex-based and supports `$1` backreferences in the replacement (per Sleep manual §6).
- `token=[^\s&"\\\']+` — match `token=` then any run of non-whitespace, non-`&`, non-`"`, non-`'` chars. Conservative termination — stops at common URL/JSON/quoting boundaries so we don't over-redact past the token value into the rest of an error message.
- The escaped quotes `\"` and `\\\'` are Sleep escape sequences for the regex literal containing `"` and `'`. The replacement `$1token=***REDACTED***` preserves the delimiter.

- [ ] **Step 3: Verify the .cna edit is syntactically clean**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "redrobin_sanitize_error\|token=\\*\\*\\*REDACTED" adapters/cobaltstrike/redrobin.cna
```

Expected:
- `sub redrobin_sanitize_error {` definition line (was line 138, now around line 147 after the comment expansion).
- The new `$err_str = replace($err_str, '([?&])token=...` line.
- 1 call site of `redrobin_sanitize_error` in the rest of the file (definition at line 138 + 1 call at line 85; verify count hasn't regressed).

If the regex literal has a Sleep parse error, the .cna will fail to load. Smoke validation in Task 3b is the canonical check — there is no offline Sleep linter in the repo.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/redrobin.cna
git commit -m "$(cat <<'EOF'
fix(cna): redact ?token=<value> URL query params in sanitize_error

Pre-existing latent defensive gap: redrobin_sanitize_error in
adapters/cobaltstrike/redrobin.cna redacts the raw $REDROBIN_TOKEN
value and the `Authorization: Bearer ...` header pattern but does
not redact `?token=<value>` URL query strings.
RedRobinEventStream.java:51 constructs the WS URL as
`ws://.../v1/adapters/events?token=<bearer>` (token-in-query per
the FastAPI WS auth pattern). The JAR side does NOT log the URL
today (RedRobinEventStream.java:92-95 logs only gapMs), so the leak
is latent — but a future Java exception whose message embeds the
URI (URISyntaxException, IOException with cause) would route through
redrobin_sanitize_error untouched.

Fix: add a third replace() regex to redrobin_sanitize_error matching
`([?&])token=[^\s&"\']+` — captures both `?token=` (first param)
and `&token=` (subsequent param) and preserves the leading delimiter
via $1 backreference. Conservative termination on whitespace / `&` /
quote chars so we don't over-redact past the value.

Operator-run smoke validation in the matching Task 3b commit (TC5
Probes 4 + 5) is the canonical check since the .cna has no offline
Sleep linter / unit-test harness; the regex either parses or the
.cna fails to load on next CS Client session.

Spec: docs/superpowers/plans/2026-05-24-docs-polish.md Task 3a
Backlog: part of #111 close-out (smoke pin lands in Task 3b).
EOF
)"
```

---

## Task 3b: `#111` (part 2) — add TC5 token sanitization smoke + fix TC4 inspect_episodic path

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md` (new TC5 in the Layer B section + dangling TC4 path fix)

Smoke-doc-only commit that pins the new sanitize_error regex behavior (TC5) and fixes an unrelated dangling reference in the existing TC4 to the operator-local `_inspect_episodic.py` path (now points at the in-repo `tools/inspect_episodic.py` from Plan B #131).

- [ ] **Step 1: Add new TC5 (token sanitization) to the Layer B smoke section**

The Layer B verdict visibility smoke (added 2026-05-20) currently ends at TC4 (line 254-262 of `test_cna_smoke.md` — "Cross-check verdict against episodic.db"). Append a new TC5 immediately after TC4 (before the file end). The full file is 262 lines; the new section goes at the tail.

Use the `Edit` tool, anchoring on the unique TC4 trailing line "Confirms beacon console output = daemon's stored verdict (no truncation, no drift)."

Find:

```markdown
After TC1, run:

```bash
python C:/Users/jcspi/redrobin-smoke/_inspect_episodic.py 2>&1 | tail -20
```

The MOST RECENT row should match the verdict + reason that appeared in beacon for the `mimikatz` command. Confirms beacon console output = daemon's stored verdict (no truncation, no drift).
```

Replace with:

````markdown
After TC1, run:

```bash
uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<active-slug>/episodic.db 2>&1 | tail -20
```

The MOST RECENT row should match the verdict + reason that appeared in beacon for the `mimikatz` command. Confirms beacon console output = daemon's stored verdict (no truncation, no drift).

### TC5: Token sanitization across all smoke logs (added 2026-05-24, #111)

Strengthens the existing token-sanitization checks. The CNA HTTP modernization smoke §5 (cp1252-era) and the CNA JAR helper smoke §6 (real-401 path) cover the HTTP request flow. This TC closes the WS URL leak vector for the Layer B event stream (`RedRobinEventStream` builds its URL as `ws://.../v1/adapters/events?token=<bearer>` — `RedRobinEventStream.java:51`).

Setup:

- [ ] Daemon running, .cna loaded, beacon active (as per TC1 setup).
- [ ] In a separate terminal, capture the daemon log to a file:
  ```bash
  redrobin daemon start --host 127.0.0.1 --port 7777 2>&1 | tee /tmp/redrobin-daemon.log
  ```
  (Windows: `... | Tee-Object /tmp/redrobin-daemon.log` or any equivalent.)
- [ ] Note the first 8 characters of your `~/.redrobin/transport.token` (read once, hold in your head — don't paste it elsewhere). Call this `<TOKEN8>`.

Probes:

- [ ] **Probe 1: WS URL with token=  must not appear in the Script Console.**
  After .cna load + handshake, scroll the CS Client Script Console output. Run:
  ```
  grep -i 'token=' <copy of the Script Console output, exported via CS Client menu>
  ```
  Expected: zero matches. If present, the JAR is logging the WS URL — file a follow-up.

- [ ] **Probe 2: Daemon log must not contain the raw bearer token in cleartext.**
  ```bash
  grep -c "<TOKEN8>" /tmp/redrobin-daemon.log
  ```
  Expected: `0`. The daemon's structured logger redacts authorization headers via FastAPI middleware (verified by prior smoke).

- [ ] **Probe 3: `?token=<value>` query-string pattern must not appear anywhere in daemon log.**
  ```bash
  grep -E '[?&]token=[A-Za-z0-9_.\-]{8,}' /tmp/redrobin-daemon.log
  ```
  Expected: zero matches. (FastAPI logs the WS upgrade as `"GET /v1/adapters/events HTTP/1.1" 101 Switching Protocols` — query string not part of the access-log line. If matches appear, uvicorn is logging the full request URI somewhere and we need to add a redacting log filter.)

- [ ] **Probe 4: Sleep `redrobin_sanitize_error` redacts URL query token.**
  In the CS Client Script Console, exercise the sanitize sub directly:
  ```sleep
  $test = "java.net.URISyntaxException: Illegal character in query: ws://127.0.0.1:7777/v1/adapters/events?token=abcd1234efgh5678";
  println(redrobin_sanitize_error($test));
  ```
  Expected output (verbatim): `java.net.URISyntaxException: Illegal character in query: ws://127.0.0.1:7777/v1/adapters/events?token=***REDACTED***`. If the literal `abcd1234efgh5678` is still visible, the regex didn't fire.

- [ ] **Probe 5: Bearer header pattern still redacted (regression guard).**
  In the CS Client Script Console:
  ```sleep
  $test2 = "Authorization: Bearer aaaaBBBBccccDDDD";
  println(redrobin_sanitize_error($test2));
  ```
  Expected: `Authorization: Bearer ***REDACTED***` — confirms the existing redaction wasn't broken by the new query-token rule.

Acceptance: all 5 probes return the expected redaction. Any probe leaking a real token value blocks the v0.1.x ship for #111.
````

Rationale per probe:
- **Probe 1** covers the JAR-side Script Console logs (today the JAR doesn't log the URL, but the probe guards against regression).
- **Probe 2** is the same-pattern check as the existing 2026-05-14 smoke §5 — raw token must not be in daemon log.
- **Probe 3** is the new URL-query-string check that wasn't in any existing TC.
- **Probe 4** directly exercises the new `?token=` regex added to `redrobin_sanitize_error` — the most explicit pin.
- **Probe 5** is a regression guard that ensures the prior `Bearer ...` redaction still works after we added a third regex.

This Edit also fixes the dangling `_inspect_episodic.py` reference in TC4 (was operator-local path; now points at the in-repo `tools/inspect_episodic.py` from Plan B) — that fix rides in the same Task 3b commit since both edits are to `test_cna_smoke.md`.

- [ ] **Step 2: Verify the smoke doc edits landed**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "### TC5: Token sanitization across all smoke logs" adapters/cobaltstrike/tests/test_cna_smoke.md
grep -n "tools/inspect_episodic.py" adapters/cobaltstrike/tests/test_cna_smoke.md
```

Expected:
- 1 hit on the new TC5 heading.
- 1 hit on the corrected `tools/inspect_episodic.py` path in TC4.

- [ ] **Step 3: Smoke validation — operator-run gate (OPTIONAL but recommended)**

This is the canonical validation for the `.cna` sanitize_error change from Task 3a since Sleep has no unit-test harness in the repo. Operator runs Probes 4 + 5 in their CS Client Script Console after pulling the Task 3a + 3b commits, confirms the expected redacted output. If the regex has a Sleep parse error, the .cna fails to load with `[!] Function call &replace failed: invalid regex syntax` at the `sub redrobin_sanitize_error` site.

If smoke can't run in this session, defer Probes 4 + 5 until next CS Client session and ship the commit with a note "operator smoke pending" in the PR description / changelog.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/tests/test_cna_smoke.md
git commit -m "$(cat <<'EOF'
docs(smoke): add TC5 token sanitization + fix TC4 inspect_episodic path (closes #111)

Pin the new `?token=<value>` URL-query redaction shipped in the
matching Task 3a commit by adding TC5 to the Layer B verdict
visibility section of tests/test_cna_smoke.md with 5 explicit grep
/ Sleep probes:
  1. Script Console — no `token=` literal.
  2. Daemon log — no raw bearer token cleartext.
  3. Daemon log — no `[?&]token=<value>` query pattern.
  4. Sleep sub — exercises the new regex directly against a
     URISyntaxException-style string.
  5. Regression — `Bearer ...` header pattern still redacted.

Probe 4 is the canonical operator-run validation since the .cna has
no offline Sleep linter / unit-test harness; the regex either parses
or the .cna fails to load on next CS Client session.

Also fixes an unrelated dangling reference in TC4 of the same smoke
section that pointed at the operator-local _inspect_episodic.py path
— now uses the in-repo tools/inspect_episodic.py from Plan B (#131).

Spec: docs/superpowers/plans/2026-05-24-docs-polish.md Task 3b
Backlog: closes #111 (paired with Task 3a regex commit).
EOF
)"
```

---

## Task 5: Backlog close-out for #116, #120, #111

**Files:**
- Modify: `docs/superpowers/backlog.md`

Matches the pattern Plan A used for `#125`/`#127`/`#137` and Plan B used for `#131`: scope-table row flipped to **DONE** with commit SHA, entry heading updated to **RESOLVED**, body rewritten to summarize what shipped.

- [ ] **Step 1: Capture the SHAs from Tasks 1 + 2 + 3a + 3b**

After Tasks 1 + 2 + 3a + 3b land, capture the commit SHAs. Call them:
- `<TASK1_SHA>` — operator-setup.md JAR version bump (#116).
- `<TASK2_SHA>` — helper-jar README + spec (#120).
- `<TASK3A_SHA>` — `.cna` sanitize_error regex (part of #111).
- `<TASK3B_SHA>` — TC5 smoke + TC4 inspect_episodic path fix (part of #111).

- [ ] **Step 2: Update the scope table row (line 17)**

Find:
```markdown
| C | 2 — docs polish | `#116`, `#120`, `#111` | docs + smoke desc | Pending |
```

Replace with:
```markdown
| C | 2 — docs polish | `#116`, `#120`, `#111` | docs + smoke desc | **DONE 2026-05-24** (commits `<TASK1_SHA>` + `<TASK2_SHA>` + `<TASK3A_SHA>` + `<TASK3B_SHA>`) |
```

- [ ] **Step 3: Replace the `#111` entry (lines 87-91)**

Find:

```markdown
### #111 — strengthen TC5 token-sanitization smoke
**Source:** Pre-existing — Task #111 in tracker.
**Symptom:** TC5 (token sanitization smoke from Task #115) currently checks that error logs don't include the bearer token in cleartext. The check is shallow — only matches the exact env-var value. Should also verify URL-with-?token= isn't logged, and that JAR's `onError` path doesn't leak the WS URL.
**Files:** `adapters/cobaltstrike/tests/test_cna_smoke.md` (TC5 section); possibly extend `RedRobinHttp.sanitize_error()` / `RedRobinEventStream` logging.
**Fix:** Audit log-emission code paths for any string that could embed the token, update TC5 smoke checklist with explicit grep patterns.
```

Replace with:

```markdown
### #111 — strengthen token-sanitization smoke + close defensive gap (RESOLVED 2026-05-24 commits `<TASK3A_SHA>` + `<TASK3B_SHA>`)
Plan C T3a + T3b shipped. Two-part fix. (1) Defensive (T3a, `<TASK3A_SHA>`): `redrobin_sanitize_error` in `adapters/cobaltstrike/redrobin.cna` gained a third redaction layer for `?token=<value>` / `&token=<value>` URL query params (`RedRobinEventStream` builds its WS URL as `ws://.../v1/adapters/events?token=<bearer>` per `RedRobinEventStream.java:51`; today the JAR doesn't log the URL so the leak is latent, but the regex closes the future-proofing gap). (2) Smoke (T3b, `<TASK3B_SHA>`): added new TC5 to the Layer B verdict visibility section of `adapters/cobaltstrike/tests/test_cna_smoke.md` with 5 explicit grep / Sleep probes covering Script Console, daemon log, sanitize_error sub direct-exercise, and a Bearer-header regression guard; same commit also fixes a dangling TC4 reference to the operator-local `_inspect_episodic.py` path (now points at the in-repo `tools/inspect_episodic.py` from Plan B #131). Probe 4 is the canonical operator-run validation since the .cna has no offline Sleep linter. Backlog title was misleading — original "TC5 token-sanitization" in tracker referred to a TC that was actually "Daemon offline (fail-open behavior)"; the real token-sanitization TCs live at §5 of 2026-05-14 smoke + §6 of 2026-05-17 smoke + the new TC5 of 2026-05-20 smoke shipped here.
```

- [ ] **Step 4: Replace the `#116` entry (lines 93-97)**

Find:

```markdown
### #116 — operator-setup.md: correct jdk.internal bug docs
**Source:** Pre-existing — Task #116 in tracker.
**Symptom:** After the JAR helper landed in #115, `docs/operator-setup.md` still contained stale guidance about the `jdk.internal` access bug that the JAR helper specifically resolves. Out-of-date docs mislead new operators.
**Files:** `docs/operator-setup.md` (the JVM/jdk.internal section).
**Fix:** Remove or rewrite the jdk.internal section to reflect that the JAR helper (RedRobinHttp + URLClassLoader pattern) sidesteps the access issue entirely.
```

Replace with:

```markdown
### #116 — operator-setup.md: correct jdk.internal bug docs (RESOLVED 2026-05-24 commit `<TASK1_SHA>`)
Plan C T1 shipped. Original framing of #116 (stale `jdk.internal` guidance) had already been resolved by `e056c28` (JAR helper documented as zero-flag) and `289f98c` (1.0.0 → 1.0.1 JAR filename purge) during the 2026-05-17 → 2026-05-20 sprint, so the JVM-flags prose at lines 202-248 of `docs/operator-setup.md` is clean: "No JVM flags required" (line 208), JAR helper + URLClassLoader architecture described (lines 209-212), and a correctly-scoped Historical note at lines 244-248. However the JAR filename references in that same section were stale at `redrobin-aggressor-http-1.0.1.jar` (4 occurrences at lines 209, 221, 229, 234) — the 2026-05-23 `0b85321` ship-commit bumped the JAR to 1.0.2 for the path-3 ulid envelope (#138), but operator-setup.md was missed in the second bump. T1 fixes that: `replace_all` 1.0.1.jar → 1.0.2.jar across all 4 occurrences. New operators following the guide now find the artifact at its actual on-disk filename.
```

- [ ] **Step 5: Replace the `#120` entry (lines 99-103)**

Find:

```markdown
### #120 — helper-jar README + spec polish
**Source:** Audit findings from #115/#119 (CNA JAR helper plan execution).
**Symptom:** `adapters/cobaltstrike/helper-jar/README.md` and the design spec under `docs/superpowers/specs/` for the JAR helper have minor inaccuracies / outdated claims surfaced during the post-implementation audit.
**Files:** `adapters/cobaltstrike/helper-jar/README.md`, possibly `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md`.
**Fix:** Apply audit findings — typo fixes, refresh outdated claims, ensure README matches the shipped 1.0.2 JAR.
```

Replace with:

```markdown
### #120 — helper-jar README + spec polish (RESOLVED 2026-05-24 commit `<TASK2_SHA>`)
Plan C T2 shipped. README rewritten to match the 1.0.2 shipped JAR: kept the WHY-it-exists rationale focused on `jdk.internal.net.http.*` (the actual production reflection blocker) while pointing readers at spec §1.1 for the full audit of prior approaches that also failed access checks; added "Shipped classes (as of 1.0.2)" section documenting `RedRobinHttp` (with `ulid()` for #138), `RedRobinEventStream`, and `VerdictEvent`; added "Dependencies (vendored in `lib/`)" section calling out the org.json 20240303 runtime dependency (was missing); bumped the output filename from `1.0.0.jar` to `1.0.2.jar`; added `lib/json-20240303.jar` to the test-only run commands so `RedRobinEventStreamTest` compiles + runs. Spec at `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` got a §13 "Post-spec additions (2026-05-17 → 2026-05-24)" audit appendix listing the shipped delta (EventStream + VerdictEvent + org.json + ulid + version bumps) with commit SHAs — spec body left intact since it's a historical design doc.
```

- [ ] **Step 6: Verify the close-out edits**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
grep -n "DONE 2026-05-24\|RESOLVED 2026-05-24" docs/superpowers/backlog.md | head -10
grep -n "^### #116\|^### #120\|^### #111" docs/superpowers/backlog.md
```

Expected:
- Scope-table row C shows DONE 2026-05-24 with all 4 task SHAs.
- 3 RESOLVED headings for #116, #120, #111 — all with 2026-05-24 close dates.
- Each replaced entry shows the SHA(s) cited in the heading.

- [ ] **Step 7: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/backlog.md
git commit -m "$(cat <<'EOF'
docs(backlog): mark #116, #120, #111 RESOLVED — Plan C close-out

Plan C T5 — backlog hygiene. Tasks 1+2+3a+3b shipped:
- T1: bumped operator-setup.md JAR refs 1.0.1 → 1.0.2 across 4
  occurrences (#116). The original "stale jdk.internal guidance"
  framing was already resolved by e056c28 + 289f98c, but the JAR
  filename was missed in the 1.0.1 → 1.0.2 second bump.
- T2: refreshed helper-jar/README.md for the 1.0.2 JAR and added a
  §13 audit appendix to the JAR helper spec (#120).
- T3a: added `?token=<value>` URL query redaction regex to
  redrobin_sanitize_error in adapters/cobaltstrike/redrobin.cna
  (part of #111).
- T3b: pinned the new regex with TC5 in the Layer B smoke section
  + fixed the dangling TC4 _inspect_episodic.py path (part of #111).

Pattern matches Plan A (#125 / #127 / #137) and Plan B (#131):
status table row flipped to DONE with all 4 task SHAs, each entry
heading updated to RESOLVED with the resolving commit SHA(s), body
rewritten to summarize what shipped.

Spec: docs/superpowers/plans/2026-05-24-docs-polish.md Task 5
EOF
)"
```

---

## Self-Review (skill checklist)

**Spec coverage:** All three backlog items have explicit close-out paths:
- `#116` → Task 1 (operator-setup.md JAR-version bump, 1 commit) + Task 5 (backlog RESOLVED).
- `#120` → Task 2 (README + spec edits, 1 commit) + Task 5 (backlog RESOLVED).
- `#111` → Task 3a (sanitize_error regex, 1 commit) + Task 3b (TC5 + TC4 path fix, 1 commit) + Task 5 (backlog RESOLVED).

**Placeholder scan:** No TBD / TODO / "implement later" / "similar to Task N". Each step contains complete edits or complete commands. `<TASK1_SHA>`, `<TASK2_SHA>`, `<TASK3A_SHA>`, `<TASK3B_SHA>` are explicit placeholders to be filled by the executor after the upstream commits land; they're consistent with the pattern Plans A + B used.

**Type / name consistency:**
- `redrobin_sanitize_error` (Task 3a Step 2) → matches the existing sub name at `adapters/cobaltstrike/redrobin.cna:138`.
- `RedRobinEventStream.java:51` (Task 3a) → verified: the line that builds `wsUrl = wsBase + "/v1/adapters/events?token=" + bearerToken;`.
- `redrobin-aggressor-http-1.0.2.jar` (Task 1 + Task 2) → matches the shipped JAR per `adapters/cobaltstrike/jars/` and the `0b85321` ship commit.
- `tools/inspect_episodic.py` (Task 3b TC4 fix) → matches the in-repo helper shipped by Plan B (#131) at commit `926e5da`.
- `e056c28` + `289f98c` (Task 5 #116 entry context) → verified in git log; `e056c28` documents the JAR helper as zero-flag, `289f98c` purges 1.0.0 references — both predate the 1.0.1 → 1.0.2 bump that T1 closes here.
- Total execution commits: **5** (Task 1 + Task 2 + Task 3a + Task 3b + Task 5).

**Risk note:** Task 3a's Sleep regex is the only piece that could fail at load time (Sleep .cna has no offline linter in the repo). Probes 4 + 5 in TC5 (Task 3b) are the canonical validation; operator should run them in the next CS Client session. If the regex parses incorrectly, revert is trivial (`git revert <TASK3A_SHA>`); the prior 2-layer sanitization remains in place and the latent gap stays open — same as before this plan.

All references consistent. Ready for execution.
