# Manual smoke test plan for `redrobin.cna`

Tests the Aggressor adapter end-to-end against a real CS 4.12 client + daemon.
**Cannot run in CI** — requires CS Client (Java GUI) and Cobalt Strike teamserver.
Per project rule, this operator-smoke procedure IS the acceptance artifact for `.cna`
changes (there is no offline Sleep linter).

> **Scope note (2026-07-26, pre-flight gate removal).** The `.cna` is now a **capture hook
> only**. It does not intercept, gate, block, annotate, or modify any command. All alias
> overrides, the `popup beacon` menu, the `/v1/verdict/quick` round-trip and the Layer B
> verdict console messages were deleted. Every test case whose subject was the gate has been
> removed from this file; what remains covers the capture path, the transport, and the
> handshake. §1 below is the acceptance for the removal itself.

## Setup

1. Start daemon:
   ```bash
   uv run redrobin daemon start --host 127.0.0.1 --port 7777
   ```
2. CS Client → Scripts → Load → select `adapters/cobaltstrike/redrobin.cna`
   (after any `.cna` edit you must **unload then reload** — a stale script looks like a code bug)
3. Verify Script Console shows `[Red Robin] handshake OK; session=ses_...`
4. Verify Script Console shows `[Red Robin] .cna loaded; capture active (observe-only, no interception).`

---

## 1. No interception — THE KEYSTONE (added 2026-07-26)

Acceptance for the pre-flight gate removal. These commands were **all** intercepted by an
`alias` override before this change; 16 of them were hard-blocked when the daemon could not
answer. Every one must now behave exactly like stock Cobalt Strike.

### 1.1 Previously-gated dangerous commands dispatch natively

- [ ] In a beacon console, run: `mimikatz sekurlsa::logonpasswords`
- [ ] Expected: the command **tasks and executes normally** — CS shows its usual
      `Tasked beacon to run mimikatz ...` and returns real output.
- [ ] Expected: **NO** `[Red Robin]` line of any kind in the beacon console — no `DANGER:`,
      no `WARN:`, no `gate UNAVAILABLE`, no `Layer B`.
- [ ] Repeat for a sample across the old tiers — each must dispatch with zero Red Robin output:
      `hashdump` · `getsystem` · `shell whoami` · `powershell Get-Process` · `screenshot`
- [ ] Confirm `help mimikatz` in the beacon console shows Cobalt Strike's own help text
      (proves no alias is shadowing the built-in).

### 1.2 Argument handling is CS-native again

The deleted aliases reconstructed args by hand (`redrobin_alias_parse`), which was a
recurring defect source. CS now parses directly — confirm both forms:

- [ ] `mimikatz` with **no** args → CS's own usage/error, NOT `[Red Robin] mimikatz requires arguments`.
- [ ] `mimikatz sekurlsa::logonpasswords` → the argument arrives intact (output is for the
      requested module, not a truncated one).
- [ ] A quoted-arg form, e.g. `mimikatz "lsadump::sam /system"` → arrives as a single argument.

### 1.3 The GUI popup is gone

- [ ] Right-click a beacon → **Access**. There is **no** "Make Token (gated)" and no
      "Run Mimikatz" item.
- [ ] The stock CS **Make Token** dialog opens and works.

### 1.4 Daemon down changes nothing about command execution

- [ ] Stop the daemon (`redrobin daemon stop`).
- [ ] Run `mimikatz sekurlsa::logonpasswords`, then `shell whoami`.
- [ ] Expected: **both execute normally.** No blocking, no `gate UNAVAILABLE`. A `warn` in the
      *Script Console* about the daemon being unreachable is expected and correct (capture is
      failing, not the command).
- [ ] Restart the daemon; capture resumes with no reload.

### 1.5 No dead-route traffic

- [ ] In the daemon log, over a full session: **zero** `POST /v1/verdict/quick`.
- [ ] In the daemon log: **zero** `GET /v1/adapters/events` (no 404s, no rejected WS upgrade,
      no reconnect loop).
- [ ] In the Script Console: no `ClassNotFoundException`, and no repeating WS/reconnect noise.

---

## 2. Capture path

### 2.1 `beacon.command_input`

- [ ] Run `pwd`, then `mimikatz sekurlsa::logonpasswords`.
- [ ] Verify `~/.redrobin/engagements/<active>/events.jsonl` has a `beacon.command_input`
      for **both** — including the formerly-gated one. (Before this change a Tier 1A block
      short-circuited before any event existed; capture should now be strictly richer.)
- [ ] Verify each carries `operator`, `command` (the verb), `raw` (the full line) and a
      `correlation_id`.

### 2.2 `beacon.command_output` (BEACON_OUTPUT filter hook)

- [ ] Run `whoami`.
- [ ] Expected: output appears in the CS console **as normal** (the hook is passthrough —
      it must always `return $2`).
- [ ] Verify `events.jsonl` contains a `beacon.command_output` with the output text, a
      `task_id`, and the **same** `correlation_id` as its `command_input`.
- [ ] **Output suppression guard:** run several commands in a row and confirm none produce
      empty output in the console. (Known landmine: an uncaught NPE inside the filter aborts
      the hook before `return $2` and silently swallows the operator's output.)

### 2.3 `beacon.initial` — new check-in

- [ ] Spawn / relink a NEW beacon. Within ~1s a `beacon.initial` is POSTed and the TUI
      Sessions panel shows it with id + hostname + user + integrity.

### 2.4 `beacon.initial` — load-time backfill

- [ ] With a beacon **already** checked in, unload and reload the `.cna`.
- [ ] Expected: the pre-existing beacon is backfilled (Sessions panel populates rather than
      showing "(no active sessions)"); daemon log shows `POST /v1/events ... 200`.

### 2.5 Self-ingestion guard

- [ ] The `BEACON_OUTPUT` hook skips emitting for lines starting `[Red Robin]`. With the gate
      gone the `.cna` no longer `blog`s anything, so this should never fire — confirm no
      `beacon.command_output` event in `events.jsonl` has a `[Red Robin]`-prefixed body.

---

## 3. Transport + handshake

### 3.1 Stock launch (no JVM flags) — KEYSTONE

Operator launches CS Client via the official launcher with **zero JVM flags**.

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

### 3.2 JAR loading

- [ ] Verify the JAR is at the expected path:
  ```bash
  ls C:/Users/jcspi/Documents/dev-projects/Red-Robin/adapters/cobaltstrike/jars/
  ```
  Expected: `redrobin-aggressor-http-1.0.2.jar`
- [ ] In Script Console after .cna load: NO ClassNotFoundException visible.
- [ ] Optional verification — type in Script Console: `println([$REDROBIN_HTTP getClass]);` → expect `class com.redrobin.aggressor.RedRobinHttp`.

### 3.3 Heartbeat round-trip — **MANUAL ONLY** (the periodic fork is disabled)

> **Do not wait for this one — it will never fire on its own.** The 30-second `fork{}` that
> drove `redrobin_heartbeat()` is **commented out** in `redrobin.cna` (see the
> `===== Heartbeat fork — DISABLED for v0.1 =====` block, immediately above
> `on script_unload`): Sleep's `fork()` builds a fully isolated scope that cannot see
> `$REDROBIN_HTTP`, so the loop was disabled pending the `lambda(...)` rewrite (#110).
> **Pre-existing condition — NOT caused by the pre-flight gate removal.** The
> `redrobin_heartbeat` sub itself is live and correct, so this step must be driven **by
> hand** (or the fork temporarily re-enabled). The coverage is kept because the
> heartbeat route is still the adapter's only liveness probe.

- [ ] In the CS Client Script Console, fire one heartbeat by hand:
  ```sleep
  redrobin_heartbeat();
  ```
- [ ] In daemon log expect: `GET /v1/adapter/heartbeat?adapter=cs-main HTTP/1.1 200 OK`
- [ ] In Script Console expect: NO `Attempted to call a non-static method on a null reference` errors.
- [ ] Negative path: stop the daemon, run `redrobin_heartbeat();` again, and expect exactly
      `[Red Robin] daemon unreachable; command capture is being dropped`. Restart the daemon.
- [ ] _Optional — only if you want the periodic path exercised:_ uncomment the `fork({...})`
      block in `redrobin.cna`, reload, wait 30-40 s, then **revert the edit**. Expect it to
      misbehave today: inside the fork `$REDROBIN_HTTP` is invisible, the GET throws, and
      `redrobin_http_get` catches it as `code => 0` — so you get a spurious
      `[Red Robin] daemon unreachable...` warn every 30 s with a healthy daemon. That is
      #110, not a regression; record and move on.

### 3.4 Actionable error logging

- [ ] Stop the daemon (`redrobin daemon stop`).
- [ ] Type any command in the beacon console (the capture emit is what triggers the HTTP call).
- [ ] In Script Console expect: `[Red Robin] daemon connection refused at http://localhost:7777 — is the daemon running?`
- [ ] Confirm the command itself still executed.
- [ ] Restart daemon. The next command must capture without a warn.

### 3.5 Handshake retry under daemon-boot race

- [ ] Stop daemon (`redrobin daemon stop`).
- [ ] Aggressor Scripts → Unload `redrobin.cna` → Load again.
- [ ] Within 1-2 seconds: `redrobin daemon start --host 127.0.0.1 --port 7777`
- [ ] Watch the Script Console — expect:
  ```
  [Red Robin] handshake attempt 1/3 failed; retrying in 500ms
  [Red Robin] handshake OK; session=ses_<uuid>
  ```
  (Or attempt 2 then attempt 3 then success, depending on timing.)

### 3.6 Handshake manifest is capture-only

The manifest is sent inline by `redrobin_handshake_with_retry` in `redrobin.cna`; check what
the daemon actually registered, not the file.

- [ ] In the daemon log / adapter session store, confirm the registered manifest declares
      exactly these capabilities — `tui_dispatch`, `output_observe`, `console_annotation`,
      `emergency_kill_switch` all `true`, and `command_modification` `false`.
- [ ] Confirm `events_emitted` lists the five capture types: `beacon.command_input`,
      `beacon.command_output`, `host.observation`, `credential.observed`, `beacon.initial`.
      (Known pre-existing divergence: `adapters/cobaltstrike/manifest.toml` still lists
      `adapter.heartbeat` as its fifth entry instead of `beacon.initial`. Only the `.cna`'s
      inline list is registered, so do NOT fail this step on that mismatch.)
- [ ] Confirm there is **no** `events_consumed` key — with the verdict WebSocket gone the
      adapter consumes nothing from the daemon; it only POSTs.

### 3.7 Java version guard (optional smoke)

Skip if no easy way to fake Java version. Otherwise:

- [ ] Temporarily hard-code `$major = 8;` in `redrobin_check_java_version`.
- [ ] Reload .cna.
- [ ] In Script Console expect: `[Red Robin] FATAL: Java ... detected. Red Robin requires Java 11+.`
- [ ] Confirm `[Red Robin] handshake OK` does NOT appear (script aborted at throw).
- [ ] Revert the test edit.

### 3.8 Reload / unload hygiene

- [ ] Unload and reload the `.cna` 3-5 times in Script Manager.
- [ ] Expected: no errors from `on script_unload` (it is a deliberate no-op now — the guard
      short-circuits because nothing assigns `$REDROBIN_EVENT_STREAM`), no thread growth, no
      duplicate handshakes accumulating sessions.

---

## 4. Token sanitization (#111)

Every probe below covers a redaction rule that survives in `redrobin_sanitize_error`.

Setup:

- [ ] Daemon running, .cna loaded, beacon active.
- [ ] Capture the daemon log to a file:
  ```bash
  redrobin daemon start --host 127.0.0.1 --port 7777 2>&1 | tee /tmp/redrobin-daemon.log
  ```
  (Windows: `... | Tee-Object /tmp/redrobin-daemon.log` or any equivalent.)
- [ ] Note the first 8 characters of your `~/.redrobin/transport.token` (read once, hold in
      your head — don't paste it elsewhere). Call this `<TOKEN8>`.

Probes:

- [ ] **Probe 1: real-401 path.** Start a throwaway 401 responder:
  ```bash
  python -c "from http.server import BaseHTTPRequestHandler,HTTPServer
  class H(BaseHTTPRequestHandler):
      def do_POST(self):
          self.send_response(401);self.send_header('Content-Type','application/json');self.end_headers()
          self.wfile.write(f'{{\"detail\":\"req-headers={dict(self.headers)}\"}}'.encode())
  HTTPServer(('127.0.0.1',9999),H).serve_forever()"
  ```
  In the CS Client Script Console:
  `$REDROBIN_URL = "http://127.0.0.1:9999"; redrobin_init_http(); redrobin_handshake_with_retry();`
  - [ ] Expect a warn with `[Red Robin] daemon returned non-2xx for /v1/adapters/connect`.
  - [ ] Verify the warn does NOT contain literal token characters (look for `***REDACTED***`
        or absence of `<TOKEN8>`).
  - [ ] Restore `$REDROBIN_URL = "http://localhost:7777"; redrobin_init_http()` and Ctrl+C the server.

- [ ] **Probe 2: daemon log must not contain the raw bearer token in cleartext.**
  ```bash
  grep -c "<TOKEN8>" /tmp/redrobin-daemon.log
  ```
  Expected: `0`.

- [ ] **Probe 3: `?token=<value>` query-string pattern must not appear anywhere in daemon log.**
  ```bash
  grep -E '[?&]token=[A-Za-z0-9_.\-]{8,}' /tmp/redrobin-daemon.log
  ```
  Expected: zero matches.

- [ ] **Probe 4: Sleep `redrobin_sanitize_error` redacts a URL query token.**
  In the CS Client Script Console:
  ```sleep
  $test = "java.net.URISyntaxException: Illegal character in query: http://127.0.0.1:7777/v1/events?token=abcd1234efgh5678";
  println(redrobin_sanitize_error($test));
  ```
  Expected output (verbatim): `java.net.URISyntaxException: Illegal character in query: http://127.0.0.1:7777/v1/events?token=***REDACTED***`.
  If the literal `abcd1234efgh5678` is still visible, the regex didn't fire.

- [ ] **Probe 5: Bearer header pattern still redacted (regression guard).**
  ```sleep
  $test2 = "Authorization: Bearer aaaaBBBBccccDDDD";
  println(redrobin_sanitize_error($test2));
  ```
  Expected: `Authorization: Bearer ***REDACTED***`.

Acceptance: all 5 probes return the expected redaction. Any probe leaking a real token value
blocks the ship for #111.

---

## 5. TUI live-state (#146 / #139 / #140 / #143)

Live-beacon acceptance for the TUI live-state cluster. Requires the TUI connected
(`redrobin tui`) and an engagement with non-empty scope. The daemon side is unit/integration
tested; this smoke is the ONLY acceptance for the `.cna` `beacon.initial` emit (#143-c2).

### 5.1 Setup
- [ ] Daemon running; engagement active with a non-empty `in_scope`.
- [ ] One active beacon already checked in BEFORE loading the `.cna`.
- [ ] In Script Manager: unload `redrobin.cna`, then reload it. Confirm `[Red Robin] handshake OK`.
- [ ] Launch / connect the TUI (`redrobin tui`).

### 5.2 #143 backfill (pre-existing beacon)
- [ ] On `.cna` (re)load, the TUI **Sessions** panel populates with the already-connected beacon —
      showing its id + hostname + user + integrity — NOT "(no active sessions)".
- [ ] Daemon log shows `POST /v1/events ... 200` for a `beacon.initial`; the engagement
      `working_memory.json` gains a `sessions` entry.

### 5.3 #143 new check-in
- [ ] Spawn / relink a NEW beacon. Within ~1s it appears in the Sessions panel.

### 5.4 #146 + #139 + #140 live updates (no reconnect)
- [ ] Run `shell whoami`, then a credential-yielding command (e.g. a hashdump in the lab).
      WITHOUT reconnecting the TUI, the Working Memory panel + Memory Inspector (Ctrl+I) update live.
- [ ] Inspector shows real scope / objectives / phase (not defaults) — proving #139 (full
      snapshot) + #146 (live re-emit).

### 5.5 #140 findings / credentials
- [ ] The Inspector shows a **CREDENTIALS** section with the captured credential, and a
      **FINDINGS** section populated by the enricher. (The `opsec_block` finding stream was
      removed with the gate — findings are enricher-sourced now.)

---

## 6. Assistant end-to-end

- [ ] With a beacon live and several commands run, open the chat and ask about a command just
      executed. The assistant answers from captured activity (proves capture → memory → chat).
- [ ] Confirm L3 episodes accumulate from `beacon.command_input`:
  ```bash
  uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<active-slug>/episodic.db 2>&1 | tail -20
  ```
  Expected: one row per command, no duplicates, and the real command verb (not `?`).

---

## Acceptance criteria

- §1 (no interception) passes in full — this is the acceptance for the gate removal.
- §2 (capture path) passes in full — this is the product.
- §3-§6 pass on real CS 4.12 + daemon. §3.3 (heartbeat) is **manual only** — it cannot pass by
  waiting, because the periodic `fork{}` is disabled in `redrobin.cna` (#110, pre-existing).
- Update this file with PASS/FAIL annotations during the smoke test.
