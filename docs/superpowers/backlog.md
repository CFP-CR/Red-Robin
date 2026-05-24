# Red Robin — Backlog (v0.1.x → v0.2)

Generated 2026-05-24 from in-session task tracker during the adapter event-stream + engagement-isolation work (commits `ec5dbe5` → `d949404`). These items were discovered while shipping the keystone v0.1 BLOCKER (#123) but were deliberately scoped out of the shipping commits to keep them atomic.

Each entry below includes the bug source (commit / smoke session), reproduction notes, and recommended fix scope. None of these block the DEF CON RTV CFP demo (2026-05-31) unless explicitly marked **Critical for demo**.

---

## Critical for demo — fix before 2026-05-31

These materially affect the live demo experience or shipping correctness.

### #133 — bare-args alias guards (RESOLVED 2026-05-24 commit `b3ed579`)
**Original symptom:** Operator types `mimikatz` (no subcommand). Layer A "novel pattern" + Layer B BLOCK verdicts surface but `&bmimikatz($1, "")` is rejected by CS Aggressor with `[!] Function call &bmimikatz failed: argument command is missing`. Beacon never executes. UX misleading (verdicts appear as if it dispatched), Layer A/B wasted on input that couldn't run anyway.
**Audit found same vulnerability in 18 other aliases** (tier1a_gate.sleep: kerberos_ticket_use, psexec, wmi, winrm, jump, spawn, bypassuac, elevate, inject, dllinject, psinject; tier1b_wrapper.sleep: shell, powershell, powerpick, execute-assembly, inline-execute, run, execute). dcsync already had its own guard; hashdump/logonpasswords/getsystem/screenshot/keylogger/screenwatch are zero-args. tier2 browserpivot/socks deferred (bare-call may be valid — proxy stop/list semantics).
**Resolution:** Added DRY helper `redrobin_alias_require_args($bid, $cmd, $args)` in tier1a_gate.sleep (loaded first by redrobin.cna so visible to tier1b). Uses Sleep's full-string-anchored `ismatch '\s*'` to detect empty/whitespace-only. On rejection: `berror($bid, "[Red Robin] " . $cmd . " requires arguments. Type 'help " . $cmd . "' in the beacon console for usage.")` + return false (caller skips both gate AND dispatch). Wrapped 19 alias bodies with the guard. dcsync's existing custom guard preserved. Cost: ~35 LOC total across 2 files.
**Smoke validation 2026-05-24 (operator-run, 7 cases):**
  - A `mimikatz` (bare): red guard message + cero POST /v1/verdict/quick ✓
  - B `shell` (bare): same ✓
  - C `spawn` (bare): same ✓
  - D `mimikatz logonpasswords`: guard pass → Layer A returned BLOCK by Sigma rule T1003.001 (rules engine working) ✓
  - E `shell whoami`: guard pass → Layer A novel pattern → Layer B WARN → dispatch → beacon output ✓
  - F `getsystem` (zero-args): guard NOT invoked (correct), Layer A/B flow normal ✓
  - G `hashdump` (zero-args): guard NOT invoked, Layer A → Layer B BLOCK normal ✓
**Bonus observations** (not bugs): Layer A Sigma rules block specific subcommands directly (e.g. `mimikatz logonpasswords` → T1003.001 BLOCK without Layer B), Layer B references engagement-summary context (`5+ shell whoami instances per episode summary`) and cross-command memory (`near-identical telemetry to already-blocked mimikatz`). v0.1 memory + summary loop functioning end-to-end.

### #134 — `$REDROBIN_HTTP null` at `redrobin.cna:83` (RESOLVED 2026-05-24 in commit `b7c7c69`)
**Original symptom:** `[!] Attempted to call a non-static method on a null reference at redrobin.cna:83` firing 2s after every beacon-output blog. Beacon output was completely suppressed because Sleep's filter semantics treat a raise inside `set BEACON_OUTPUT { ... }` as "filter aborted" → the body's `return $2;` never executes.
**Actual root cause (verified):** NOT the hook scope. The hooks themselves (`set BEACON_OUTPUT`, `on beacon_tasked`) run on the main script thread with full global visibility. The bug was an internal `fork({...}, \$body)` inside `redrobin_push_event_async` at lines 232-238 that captured only `$body`, not `$REDROBIN_HTTP`. Inside the fork's isolated ScriptInstance scope, `redrobin_http_post`'s `global('$REDROBIN_HTTP ...')` lookup returned null. Same root cause as Task #110 (heartbeat fork) + T11 (Layer B verdict fork).
**Fix (commit `b7c7c69`):** removed the internal `fork({...})` entirely; `redrobin_push_event_async` now POSTs synchronously. Cost: up to ~200ms blocking per beacon event (JAR enforces HTTP timeout). Acceptable for v0.1; filable as v0.2 if mass-enum workloads expose visible latency. The catch/swallow stays in place so daemon outages don't break beacon UX.
**Follow-on bug surfaced:** see #138 — the events DO now reach the daemon, but get 400 Bad Request because the wire envelope (id, ts) and event payloads (operator/command/task_id missing) don't match the v0.1 schemas. Path-3 telemetry is broken at the schema layer; demo unaffected because errors are swallowed at the HttpException catch.

---

### #138 — path-3 observation events fail envelope + payload schema (RESOLVED 2026-05-24 commits `006e83a`→`cf37d36`)
**Original symptom:** After #134 unblocked POSTs to `/v1/events`, every one returned `400 Bad Request`. Four schema mismatches verified by curl: envelope `id` was Java UUID (schema wants ULID), envelope `ts` was Java `Date.toString()` (schema wants RFC3339), `beacon.command_input` was missing `operator` + `command`, `beacon.command_output` was missing `task_id`.
**Resolution:** Five-commit subagent-driven sequence (`006e83a` ULID tests RED → `bc72cac` ULID impl GREEN → `5fce8f0` build scripts 1.0.2 → `0b85321` ship 1.0.2 JAR → `cf37d36` atomic .cna refactor). Added `RedRobinHttp.ulid()` instance method (Crockford b32, 48-bit ts + 80-bit randomness, JDK-only — `java.security.SecureRandom`). Replaced `dstamp(ticks())` with `[[java.time.Instant now] toString]`. Split monolithic `redrobin_push_event_async` into `redrobin_envelope` + `redrobin_emit_command_input` + `redrobin_emit_command_output` with schema-required fields encoded in each sub's signature. Switched `on beacon_tasked` → `on beacon_input` (gains `operator`=$2, `raw`=$3, command=first token of $3, signatures verified against vysecurity/AggressorScripts-1/All_In_One.cna). `set BEACON_OUTPUT` now surfaces `$4` (jobid from CS Aggressor — verified via `default.cna:807-810`) as `task_id`.
**Smoke validation 2026-05-24 (operator-run):** `shell whoami` on a live beacon → daemon log shows 4× `POST /v1/events HTTP/1.1" 200 OK`, zero 400s. `jcsp\jcspi` visible in beacon console. tc4-smoke episodic.db gains rows 42+43 for Layer A ESCALATE + Layer B WARN. Working memory reducer extracted 1 credential + 3 findings from path-3 events. Script Console clean (no NPE, no daemon-non-2xx warns).

---

## v0.1.x hardening — schedule for next milestone

Not blocking the demo, but visible operator pain points or known latent bugs.

### #125 — `RedRobinEventStream.close()` on script unload
**Source:** T11 code review 2026-05-20
**Symptom:** `redrobin.cna` reload leaks the previous JAR's daemon thread + WebSocket. Each reload accumulates a stale `RedRobinEventStream` instance.
**Files:** `adapters/cobaltstrike/redrobin.cna` (no `on script_unload` hook exists).
**Fix:** Add `on script_unload { if ($REDROBIN_EVENT_STREAM !is $null) { [$REDROBIN_EVENT_STREAM close]; } }`. ~3 LOC.

### #127 — WS endpoints don't handle `bus.subscribe()` exceptions cleanly
**Source:** T6 code review 2026-05-20
**Symptom:** Both `tui_ws.py:27` and `adapter_events_ws.py:31` call `queue = await bus.subscribe()` AFTER `websocket.accept()` but outside `try`. If `subscribe()` raises, `finally: bus.unsubscribe(queue)` hits `UnboundLocalError`. Pre-existing pattern; B2 didn't introduce it.
**Files:** Both WS endpoints.
**Fix:** Initialize `queue = None` before the call, guard `unsubscribe` with `if queue is not None`.

### #129 — Layer B is over-cautious WARN for benign recon
**Source:** T14 smoke 2026-05-22 — `shell ipconfig` and `shell tasklist /v` returned Layer B WARN solely on the cmd.exe-child-spawn pattern.
**Symptom:** Operator running standard recon sees WARN noise that drowns out high-signal verdicts. Claude flags "cmd.exe spawned by beacon" universally regardless of whether the actual command is risky.
**Files:** Layer B prompt template (find in `redrobin/application/layer_b_engine.py` SYSTEM_PROMPT).
**Fix:** Distinguish in the prompt: "the shell vehicle has a small detection cost — mention once per session, not per command" from "the payload command itself is actually risky — per-command WARN". Cross-reference Layer A's rule_id; when Layer A returned PASS, Layer B should default toward PASS unless something genuinely warrants escalation.

### #130 — Layer B fires even when Layer A is silent
**Source:** T14 smoke 2026-05-22
**Symptom:** `shell ipconfig` and `shell tasklist /v` did NOT show "[Red Robin] novel pattern — Layer B reviewing async" but DID get Layer B WARN ~10s later. Either Layer A returned ESCALATE with a different `reason` (tier1a_gate.sleep prints "novel pattern" preamble conditionally on reason text), OR Layer B has a trigger path beyond ESCALATE.
**Files:** `redrobin/application/verdict_pipeline.py:134` (Layer B trigger condition), `adapters/cobaltstrike/aliases/tier1a_gate.sleep` (novel-pattern preamble logic).
**Fix:** Trace `_run_layer_b` invocations vs the Layer A reason text the alias is checking. Either fix the alias preamble to match all ESCALATE paths, OR document that Layer B can fire on other verdict types.

### #131 — `_inspect_episodic.py` crashes on Windows cp1252
**Source:** T14 smoke 2026-05-22, `UnicodeEncodeError: '→'` from print statements when Layer B reason contains `→`.
**Symptom:** PowerShell's default cp1252 encoding crashes the operator helper script on common Unicode chars from Layer B reasoning.
**Files:** `C:\Users\jcspi\redrobin-smoke\_inspect_episodic.py` (operator-local, NOT in repo).
**Fix:** Add `sys.stdout.reconfigure(encoding="utf-8")` at top of script. Trivial one-line fix; operator can do it themselves.

### #137 — broaden `start_engagement` exception handling (yaml + OSError)
**Source:** B2 code review 2026-05-24
**Symptom:** `lifespan_helpers.py:start_engagement` only catches `FileNotFoundError`. A malformed `engagement.yaml`, permission error, or unicode decode error crashes daemon startup with a stack trace.
**Files:** `redrobin/api/lifespan_helpers.py:start_engagement` exception handler.
**Fix:** Either (a) broaden the `except` to also catch `yaml.YAMLError` + `OSError` with `logger.warning` + wildcard fallback (consistent with current FileNotFoundError handler), OR (b) document "fail fast" as the deliberate policy and update the docstring. Pick one.

---

## v0.2 — architectural cleanup

Larger refactors. Defer past v0.1.x.

### #126 — `beacon_id` vs `bid` naming inconsistency across protocol schemas
**Source:** T4 code review 2026-05-20
**Symptom:** `opsec_verdict.schema.json` uses `beacon_id` (Daemon code path emits this). Sibling event schemas (`beacon_command_output`, `beacon_command_input`, `beacon_initial`, `credential_observed`, `host_observation`) all use `bid` (3 letters). Protocol-wide naming is split across event types.
**Files:** `protocol/schemas/v0.1/events/*.schema.json` + Daemon emitters.
**Fix:** Pick one canonical name (probably `bid` for terseness, but `beacon_id` is more self-documenting). Migrate the other side. Touches schema files + Daemon publishers + JAR parser (`VerdictEvent.beaconId`).

### #135 — two `Engagement` classes with disjoint YAML schemas
**Source:** B2 code review 2026-05-24
**Symptom:** Two distinct classes both named `Engagement` coexist:
  1. `redrobin.domain.engagement.Engagement` — used by `EngagementService` (CLI). Fields: `.id`, `.client`. Writes YAML with `id`, `client`, `scope.in_scope`, `objectives`.
  2. `redrobin.application.engagement_loader.Engagement` — used by daemon lifespan. Fields: `.slug`, `.name`, `.in_scope`, `.out_of_scope`, `.summary`. Reads YAML with `slug`, `name`, top-level `in_scope`.
Result: `redrobin engagement new X` writes a YAML the loader can't fully consume — daemon loads the engagement with empty `in_scope`/`out_of_scope`/`summary` regardless of what the operator put in the file.
**Files:** Both `Engagement` classes + `EngagementLoader` + `EngagementService.new()` template.
**Fix:** Unify on one `Engagement` class with one YAML schema. Either migrate the writer (`EngagementService.new()` template) or the loader. Touch test fixtures that assume the current shape. Includes a `redrobin migrate engagement-yaml` step if pre-2026-05-24 YAML files need to be normalized.

---

## Status of related closed tasks (for reference)

- **#123** — v0.1.x BLOCKER: wire .cna to receive opsec.verdict (Layer B) events. **CLOSED** — keystone TC1+TC2+TC3 pass via beacon console + TUI. TC4 cross-check pending final verify post-B2 isolation work.
- **#128** — T14 operator-run smoke gate. **In progress** — TC4 retry pending.
- **#132** — reducer no-ops when no engagement set. **CLOSED** by B2 (`8e2d280`).
- **#136** — clarify single-root episodic.db architecture. **CLOSED** — superseded by B2 implementation of per-engagement persistence + B3 docs.

---

## How to read this file

Each entry has:
- A `#NNN` task ID from the in-session tracker (may not match GitHub issues if filed separately).
- **Source**: the commit, code review, or smoke session that surfaced the bug.
- **Symptom**: what an operator or test sees.
- **Files**: pointers into the codebase.
- **Fix**: recommended scope, ranked toward cheapest viable path.

When picking the next slice: do **Critical for demo** first, then v0.1.x hardening, then v0.2 cleanup. Each item is sized to be a single atomic commit unless explicitly marked otherwise.
