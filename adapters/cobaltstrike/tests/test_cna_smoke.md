# Manual smoke test plan for `redrobin.cna`

Tests the Aggressor adapter end-to-end against a real CS 4.12 client + daemon.
**Cannot run in CI** — requires CS Client (Java GUI) and Cobalt Strike teamserver.

## Setup

1. Start daemon:
   ```bash
   uv run redrobin daemon start
   ```
2. CS Client → Scripts → Load → select `adapters/cobaltstrike/redrobin.cna`
3. Verify console shows `[Red Robin] handshake OK; session=ses_...`
4. Verify console shows `[Red Robin] .cna loaded; gate active.`

## Test cases

### TC1: Tier 1A built-in intercepts (`mimikatz`)
- Type in beacon console: `mimikatz logonpasswords`
- Expected: console shows `[!] [Red Robin] DANGER: lsass touch ...`
- Verify: command does NOT appear in beacon task list (never reached teamserver)
- Verify: `~/.redrobin/engagements/<active>/events.jsonl` does NOT have a beacon.command_input for it (alias body short-circuited before BEACON_TASKED could fire)

### TC2: Tier 1B wrapper intercepts (`shell nxc ldap`)
- Type: `shell nxc ldap 192.168.1.0/24 -u user -p pass`
- Expected: console shows `[*] [Red Robin] WARN: ...` (per rule `domain_admin_enumeration` or wrapper_patterns)
- Verify: command DOES reach teamserver (PASS-with-WARN)
- Verify: `events.jsonl` has the command_input event

### TC3: Pass-through (`pwd`)
- Type: `pwd`
- Expected: no Red Robin annotation
- Verify: `pwd` runs normally; events.jsonl captures the output via BEACON_OUTPUT hook

### TC4: BEACON_OUTPUT observation
- Type: `whoami`
- Expected: output appears in CS console as normal (passthrough)
- Verify: `events.jsonl` contains beacon.command_output with the output text

### TC5: Daemon offline (fail-open behavior)
- Stop daemon
- Type: `mimikatz logonpasswords`
- Expected: alias body's HTTP call times out (200ms hard cap); gate falls open; command goes through
- Document: this is the deliberate fail-open per spec §1.2.1 Path 1 design

### TC6: Right-click GUI popup
- Right-click a beacon → Access → "Run Mimikatz (gated)"
- Expected: daemon returns BLOCK; berror shown; mimikatz NOT executed via GUI either
- Verify: popup_beacon.sleep alias does intercept the GUI click

## Phase 1 Spike dependencies

This smoke test plan assumes the canonical alias body pattern works as
documented in `docs/aggressor-alias-args.md`. Before relying on TC1/TC2 results
for production deployment, operator MUST run the Phase 1 spikes against a real
CS 4.12 client:

- `adapters/cobaltstrike/spike/test_args.cna` — alias arg semantics (Spike 1)
- `adapters/cobaltstrike/spike/test_http_latency.cna` — HTTP round-trip
  latency budget (Spike 2)

Update `docs/aggressor-alias-args.md` with the observed results and check off
"Spike 1" + "Spike 2" status boxes. Spike validation gates production
deployment but NOT v0.1 ship readiness.

## Acceptance criteria
- All 6 TCs pass on real CS 4.12 + daemon
- Operator updates `docs/aggressor-alias-args.md` to check off "Spike 1" + "Spike 2" if not already
- Update this file with PASS/FAIL annotations during the smoke test

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
- [ ] In beacon console expect: `[!] [Red Robin] DANGER: <reason from rule>` (BLOCK or WARN -- the rule fires and the stateless `ismatch` verdict parse extracts the action). NOT a silent dispatch. Do NOT revert this parse to `hasmatch`: `hasmatch` is a stateful iterator that toggles true/false on repeated identical responses, which silently fails the gate open (see "Plan G -- gate reliability smoke" below, #144).

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

---

## Layer B prompt tuning smoke (added 2026-05-24, #129)

These cases verify the Plan E SYSTEM_PROMPT tuning at
`redrobin/application/layer_b_engine.py:42-64` (commit `dee86f5`).
The prompt now defaults to PASS for standard recon and reserves WARN/
BLOCK for risky payloads. Requires daemon running, CS Client connected
with `redrobin.cna` loaded, a real beacon already callbacked.

**Acceptance criterion:** all 5 cases report the expected verdict.
If any case reports a verdict different from the expected, Plan E's
backlog close-out (Task 4) MUST NOT fire — the prompt needs further
tuning first.

**Pre-flight:** before running, restart the daemon so the new
SYSTEM_PROMPT loads. The Anthropic 1h prompt cache is invalidated by
the change; the first ~5 Layer B calls will see
`cache_creation_input_tokens > 0` — this is expected.

```bash
redrobin daemon stop
redrobin daemon start --host 127.0.0.1 --port 7777
```

Verify the daemon log shows `INFO: ... Application startup complete`
and the CS Client Script Console shows `[Red Robin] handshake OK;
session=ses_<uuid>`.

### T3-S1: `shell ipconfig` (STANDARD_RECON — expected PASS)

- [ ] Type in beacon console: `shell ipconfig`
- [ ] Within ~1s, Layer A line appears: `[Red Robin] novel pattern — Layer B reviewing async` (the gate preamble — `shell ipconfig` doesn't match any rule).
- [ ] Within ~30s, **NO** `[Red Robin] Layer B WARN:` line should appear. Layer B PASS is silent (per `verdict_pipeline.py:169` — only WARN/BLOCK emit retroactive lines).
- [ ] Cross-check: `uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<active-slug>/episodic.db 2>&1 | tail -5` should show a Layer B row with `verdict='PASS'` and a reason that includes wording like "standard recon" or "no payload-side risk".
- [ ] **Paste actual result here:** `<operator-fills: PASS | WARN | BLOCK | timed-out>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

### T3-S2: `shell tasklist /v` (STANDARD_RECON — expected PASS)

- [ ] Type in beacon console: `shell tasklist /v`
- [ ] Within ~1s, Layer A novel-pattern preamble appears.
- [ ] Within ~30s, **NO** Layer B WARN line should appear.
- [ ] Cross-check: episodic.db row should show `verdict='PASS'` with a recon-style reason.
- [ ] **Paste actual result here:** `<operator-fills>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

### T3-S3: `shell whoami` (STANDARD_RECON — expected PASS)

- [ ] Type in beacon console: `shell whoami`
- [ ] Within ~1s, Layer A preamble appears.
- [ ] Within ~30s, **NO** Layer B WARN line should appear.
- [ ] Cross-check: episodic.db row should show `verdict='PASS'`.
- [ ] **Paste actual result here:** `<operator-fills>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

Note: this is the case the prior smoke (2026-05-18, see TC2 above) observed Layer B returning WARN for solely on the cmd.exe-child-spawn pattern. T3-S3 is the **direct regression check** for #129's original symptom.

### T3-S4: `shell mimikatz logonpasswords` (RISKY_PAYLOAD / UNAMBIGUOUSLY_DANGEROUS — expected BLOCK)

- [ ] Type in beacon console: `shell mimikatz logonpasswords`
- [ ] Within ~1s, expect either:
  - `[Red Robin] DANGER: lsass touch ...` (Layer A's `mimikatz_logonpasswords` rule fires directly via the Tier 1A intercept on the `mimikatz` alias — but `shell mimikatz` may bypass the intercept if the operator uses the raw shell wrapper).
  - Or Layer A novel-pattern preamble if the shell-wrapped form doesn't hit the same rule pattern.
- [ ] Within ~30s, Layer B retroactive line appears: `[Red Robin] Layer B BLOCK: <reason about LSASS / credential extraction / T1003.001>`. Layer B MUST NOT downgrade this to PASS just because the new prompt defaults toward PASS — the UNAMBIGUOUSLY_DANGEROUS category is explicit about credential dumping.
- [ ] **Paste actual result here:** `<operator-fills>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

> **Disambiguation:** If you see `[Red Robin] DANGER: lsass touch` (or similar), Tier 1A intercept fired (Layer A blocked the bare `mimikatz` alias name). If you see `Layer A novel pattern — Layer B reviewing async` preamble followed by Layer B's BLOCK verdict, the shell-wrapped form fell through to Layer B. **Either path is acceptable for this smoke** — the test asserts Layer B BLOCKs the shell-wrapped form when it reaches Layer B.

This case is the **over-permissiveness guard**: if T3-S4 returns PASS, the tuning went too far and Plan E's prompt change needs to be rebalanced before Task 4 commits.

### T3-S5: `shell net group "Domain Admins" /domain` (RISKY_PAYLOAD — expected WARN, or BLOCK if Layer A fires first)

- [ ] Type in beacon console (note the literal quotes — operator should escape per the beacon's parser): `shell net group "Domain Admins" /domain`
- [ ] Within ~1s, expect Layer A `[Red Robin] DANGER` or `WARN` from the `domain_admin_enumeration` rule (`rules/shared/domain_admin_enumeration.yaml`, severity WARN, `arg_pattern: '(?i)net\s+group\s+"?Domain Admins"?'`). The shell command WILL match this regex.
- [ ] Because Layer A fires a verdict (not ESCALATE), Layer B does NOT run for this case (verified: `verdict_pipeline.py:134` gates Layer B on `verdict.action == VerdictAction.ESCALATE`). The retroactive Layer B line should NOT appear.
- [ ] **Paste actual result here:** `<operator-fills (Layer A verdict)>`
- [ ] **Paste Layer A reason text here:** `<operator-fills>`

T3-S5 is included as a **control case** — it verifies the existing Layer A coverage of `domain_admin_enumeration` still fires (so Plan E hasn't accidentally broken Layer A), and it confirms Layer B is silent when Layer A already spoke.

### Operator decision

After all 5 cases run:
- **All cases PASS as expected** → proceed to Plan E Task 4 (backlog close-out).
- **T3-S1 / S2 / S3 still WARN** → Plan E's prompt tuning was insufficient; iterate on Task 1's AFTER block (add more explicit anti-patterns or escalate to few-shot examples) and re-run the smoke.
- **T3-S4 returns PASS** → tuning is over-permissive; re-emphasize the UNAMBIGUOUSLY_DANGEROUS category, especially LSASS / credential dumping, and re-run.
- **Daemon errors / no Layer B response** → check daemon log for Anthropic API errors, KB load failures, or working-memory drift. NOT a prompt issue; file a separate bug.

---

## Plan G -- gate reliability smoke (2026-05-25, #144 + #141)

This is the live-beacon acceptance for #144 (the gate must NEVER silently fail open on a dangerous command) and #141 (the Layer B retroactive verdict must carry the real command verb, not `?`). Run on a live Cobalt Strike beacon, daemon on `localhost:7777`, `redrobin.cna` freshly unloaded then reloaded in Script Manager, and an engagement whose scope is non-empty.

**Root cause this smoke guards against:** Sleep `hasmatch` is a stateful iterator -- used as a boolean on repeated identical verdict responses it toggled true/false, so every other `mimikatz` fell through to PASS. The fix is stateless `ismatch`; this smoke guards the regression.

Per project rule, this operator-smoke procedure IS the acceptance artifact for `.cna` changes (there is no offline Sleep linter). The results below were recorded on the 2026-05-25 acceptance run (commits `e68c01e`, `7acd5a3`, `87f1b95`).

### 1. Setup

- [ ] Daemon running: `redrobin daemon start --host 127.0.0.1 --port 7777`.
- [ ] In CS Client Script Manager: unload `redrobin.cna`, then reload it.
- [ ] Confirm Script Console shows `[Red Robin] handshake OK; session=ses_<uuid>` then `[Red Robin] .cna loaded; gate active.`
- [ ] One active beacon callbacked.
- [ ] Active engagement scope is non-empty (verify the engagement has at least one in-scope target).

### 2. Toggle regression (THE #144 guard)

- [ ] In the beacon console, type `mimikatz logonpasswords` repeatedly -- at least 10 times in a row (the recorded run did 18).
- [ ] Expected: EVERY invocation shows `[-] [Red Robin] DANGER: ...`.
- [ ] Expected: NONE show `Tasked beacon to run mimikatz` and NONE produce credential output.
- [ ] A single execution = FAIL (that is the old `hasmatch` toggle re-appearing).
- [ ] **RECORDED RESULT:** 18/18 BLOCKED, zero toggle. (Pre-fix behaviour was ~50% -- a perfect BLOCK/EXEC alternation driven by the stateful `hasmatch` iterator.)

### 3. Benign pass-through

- [ ] In the beacon console, type `shell whoami`.
- [ ] Expected: `[Red Robin] novel pattern -- Layer B reviewing async` preamble, THEN the command executes and returns `domain\user`.
- [ ] **RECORDED RESULT:** PASS.

### 4. Daemon-down fail-CLOSED for dangerous

- [ ] Stop the daemon (Ctrl+C in the daemon terminal).
- [ ] In the beacon console, type `mimikatz logonpasswords`.
- [ ] Expected: `[-] [Red Robin] gate UNAVAILABLE (daemon unreachable) -- BLOCKING mimikatz ...`.
- [ ] Expected: NO execution.
- [ ] **RECORDED RESULT:** PASS (fail-closed).

### 5. Daemon-down warn-proceed for benign

- [ ] With the daemon still down, type `shell whoami`.
- [ ] Expected: `[Red Robin] gate UNAVAILABLE (daemon unreachable) -- shell NOT vetted, proceeding ...`, THEN the command executes.
- [ ] **RECORDED RESULT:** PASS (warn-proceed).

### 6. GUI popup fail-closed

- [ ] With the daemon still down, right-click the beacon -> Access -> "Run Mimikatz" (then repeat for "Make Token (gated)").
- [ ] Expected: each shows `[-] [Red Robin] gate UNAVAILABLE ... BLOCKING ...`; no execution. (Covers `popup_beacon.sleep`.)
- [ ] **RECORDED RESULT:** covered by the fail-closed branches added in `7acd5a3`.

### 7. #141 end-to-end (Layer B retro verdict carries the real command)

- [ ] Restart the daemon (`redrobin daemon start --host 127.0.0.1 --port 7777`).
- [ ] Run a HARMLESS command that ESCALATEs to a Layer B WARN, e.g. `shell findstr /si password *.txt`. Do NOT use a real LSASS dump: on ESCALATE the gate proceeds before Layer B returns.
- [ ] After ~10s, inspect the engagement `episodic.db`:
  ```bash
  uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<active-slug>/episodic.db 2>&1 | tail -20
  ```
- [ ] Expected: the Layer B retroactive row's `command` is the real verb (e.g. `shell`), NOT `?`.
- [ ] **RECORDED RESULT:** PASS -- zero new `command='?'` rows after the fix (commit `e68c01e`).

### Acceptance

ALL steps must pass on a live beacon. This section re-runs to green after any future gate or `.cna` change.
