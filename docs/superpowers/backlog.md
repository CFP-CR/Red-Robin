# Red Robin — Backlog (v0.1.x → v0.2)

Generated 2026-05-24 from in-session task tracker during the adapter event-stream + engagement-isolation work (commits `ec5dbe5` → `d949404`). Updated 2026-05-24 with current-sprint scope after critical-for-demo bugs cleared.

Each entry below includes the bug source (commit / smoke session), reproduction notes, and recommended fix scope. None of these block the DEF CON RTV CFP demo (2026-05-31) unless explicitly marked **Critical for demo**.

---

## Current sprint scope (2026-05-24) — Rounds 1-4

Decided 2026-05-24 to clear the v0.1.x hardening + docs + Layer B + obsolete-cleanup backlog before pivoting to the real-time assistant work. Each Plan runs through `writing-plans` → `auditing-plans` → `subagent-driven-development` (Opus 4.7) workflow.

| Plan | Round | Items | Effort | Status |
|---|---|---|---|---|
| A | 1 — defensive hardening | `#125`, `#127`, `#137` | ~30 LOC, ~1.5h | **DONE 2026-05-24** (4 commits `7a75836`→`b8be0d8`) |
| B | 1 — operator helper | `#131` | ~10 LOC + decision | **DONE 2026-05-24** (commit `926e5da`) |
| C | 2 — docs polish | `#116`, `#120`, `#111` | docs + smoke desc | **DONE 2026-05-24** (commits `62b5cbc` + `3147629` + `58af456` + `9667668`) |
| D | 3 — Layer B trigger investigation | `#130` | read-only audit, ~30 min | **DONE 2026-05-24** (commit `83f696f`) |
| E | 3 — Layer B prompt tuning (CONDITIONAL on D) | `#129` | prompt engineering, ~1-2h | Pending |
| F | 4 — close obsoletes | `#110`, `#112` | verify + close, ~15 min | Pending |

**Round 5-7 (refactors + v0.2 + infrastructure) deferred** to post-demo / separate sprint. See "Deferred — post-Round 4" section below.

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

### #125 — `RedRobinEventStream.close()` on script unload (RESOLVED 2026-05-24 commit `b8be0d8`)
Plan A T4 shipped. Added `on script_unload { if ($REDROBIN_EVENT_STREAM !is $null) { [$REDROBIN_EVENT_STREAM close]; } }` to redrobin.cna. Verified close() is idempotent (volatile shutdown flag handles repeated calls). Citation: Aggressor Script Manual (Fortra) + community precedent in harleyQu1nn/AggressorScripts.

### #127 — WS endpoints don't handle `bus.subscribe()` exceptions cleanly (RESOLVED 2026-05-24 commit `ae65a20`)
Plan A T3 shipped. Both `tui_ws.py` and `adapter_events_ws.py`: initialized `queue = None` before try, moved `bus.subscribe()` inside try, guarded `finally` with `if queue is not None`. Happy path tests (6/6 WS) still green; no regressions in 187-test integration suite.

### #129 — Layer B is over-cautious WARN for benign recon
**Source:** T14 smoke 2026-05-22 — `shell ipconfig` and `shell tasklist /v` returned Layer B WARN solely on the cmd.exe-child-spawn pattern.
**Symptom:** Operator running standard recon sees WARN noise that drowns out high-signal verdicts. Claude flags "cmd.exe spawned by beacon" universally regardless of whether the actual command is risky.
**Files:** Layer B prompt template (find in `redrobin/application/layer_b_engine.py` SYSTEM_PROMPT).
**Fix:** Distinguish in the prompt: "the shell vehicle has a small detection cost — mention once per session, not per command" from "the payload command itself is actually risky — per-command WARN". Cross-reference Layer A's rule_id; when Layer A returned PASS, Layer B should default toward PASS unless something genuinely warrants escalation.

### #130 — Layer B fires even when Layer A is silent (RESOLVED 2026-05-24 commit `83f696f`)
Plan D shipped the investigation report at `docs/investigations/2026-05-24-layer-b-triggers.md`. Both #130 hypotheses ruled out: (A) the preamble's conditional at `redrobin.cna:228` is action-equality only, never reason-text-gated; (B) `_run_layer_b` has exactly one caller (`verdict_pipeline.py:139`), gated on `verdict.action == ESCALATE` — Layer B does NOT fire on `/v1/events` ingestion, the reducer, or any other path. Most likely actual root cause: client-side 200ms HTTP timeout in `RedRobinHttp.java:30,37` while the daemon completed Layer A → ESCALATE and scheduled Layer B before the response returned. The retroactive WARN reached the beacon console ~10s later via the WS event stream (`redrobin.cna:475-486`), independent of the gate POST. Backlog entry mis-cited two facts (corrected in the report): preamble lives in `redrobin.cna:229` (not `tier1a_gate.sleep`), and the preamble conditional is action-equality only (not reason-text-gated). Recommended fix scoped for a future Plan G: ~5 LOC in `redrobin.cna` to surface gate degradation to the beacon console; not implemented here. Plan E (#129) scope confirmed unchanged — pure prompt tuning at `layer_b_engine.py:SYSTEM_PROMPT`.

### #131 — `_inspect_episodic.py` crashes on Windows cp1252 (RESOLVED 2026-05-24 commit `926e5da`)
Plan B T1 shipped. Created `tools/inspect_episodic.py` — UTF-8-safe in-repo replacement for the operator's local `_inspect_episodic.py`. Adds `sys.stdout.reconfigure(encoding="utf-8")` at start (fixes the cp1252 crash on Layer B Unicode reasons), CLI arg for DB path with `~/.redrobin/episodic.db` as the default, and an explicit missing-DB error instead of sqlite3 auto-creating an empty file. T2 updated `docs/operator-setup.md` to point at the in-repo helper.

### #137 — broaden `start_engagement` exception handling (RESOLVED 2026-05-24 commits `7a75836` + `c92710a`)
Plan A T1+T2 shipped (TDD red→green). `lifespan_helpers.py:start_engagement` now catches `yaml.YAMLError`, `OSError`, `UnicodeDecodeError` in addition to `FileNotFoundError`. Same fallback (engagement=None, wildcard scope) with a distinct log line that names the exception type for diagnosis. Regression coverage: `tests/integration/test_engagement_malformed_yaml.py` (2 cases — malformed YAML + bad-UTF-8 encoding).

### #111 — strengthen token-sanitization smoke + close defensive gap (RESOLVED 2026-05-24 commits `58af456` + `9667668`)
Plan C T3a + T3b shipped. Two-part fix. (1) Defensive (T3a, `58af456`): `redrobin_sanitize_error` in `adapters/cobaltstrike/redrobin.cna` gained a third redaction layer for `?token=<value>` / `&token=<value>` URL query params (`RedRobinEventStream` builds its WS URL as `ws://.../v1/adapters/events?token=<bearer>` per `RedRobinEventStream.java:51`; today the JAR doesn't log the URL so the leak is latent, but the regex closes the future-proofing gap). (2) Smoke (T3b, `9667668`): added new TC5 to the Layer B verdict visibility section of `adapters/cobaltstrike/tests/test_cna_smoke.md` with 5 explicit grep / Sleep probes covering Script Console, daemon log, sanitize_error sub direct-exercise, and a Bearer-header regression guard; same commit also fixes a dangling TC4 reference to the operator-local `_inspect_episodic.py` path (now points at the in-repo `tools/inspect_episodic.py` from Plan B #131). Probe 4 is the canonical operator-run validation since the .cna has no offline Sleep linter. Backlog title was misleading — original "TC5 token-sanitization" in tracker referred to a TC that was actually "Daemon offline (fail-open behavior)"; the real token-sanitization TCs live at §5 of 2026-05-14 smoke + §6 of 2026-05-17 smoke + the new TC5 of 2026-05-20 smoke shipped here.

### #116 — operator-setup.md: correct jdk.internal bug docs (RESOLVED 2026-05-24 commit `62b5cbc`)
Plan C T1 shipped. Original framing of #116 (stale `jdk.internal` guidance) had already been resolved by `e056c28` (JAR helper documented as zero-flag) and `289f98c` (1.0.0 → 1.0.1 JAR filename purge) during the 2026-05-17 → 2026-05-20 sprint, so the JVM-flags prose at lines 202-248 of `docs/operator-setup.md` is clean: "No JVM flags required" (line 208), JAR helper + URLClassLoader architecture described (lines 209-212), and a correctly-scoped Historical note at lines 244-248. However the JAR filename references in that same section were stale at `redrobin-aggressor-http-1.0.1.jar` (4 occurrences at lines 209, 221, 229, 234) — the 2026-05-23 `0b85321` ship-commit bumped the JAR to 1.0.2 for the path-3 ulid envelope (#138), but operator-setup.md was missed in the second bump. T1 fixes that: `replace_all` 1.0.1.jar → 1.0.2.jar across all 4 occurrences. New operators following the guide now find the artifact at its actual on-disk filename.

### #120 — helper-jar README + spec polish (RESOLVED 2026-05-24 commit `3147629`)
Plan C T2 shipped. README rewritten to match the 1.0.2 shipped JAR: kept the WHY-it-exists rationale focused on `jdk.internal.net.http.*` (the actual production reflection blocker) while pointing readers at spec §1.1 for the full audit of prior approaches that also failed access checks; added "Shipped classes (as of 1.0.2)" section documenting `RedRobinHttp` (with `ulid()` for #138), `RedRobinEventStream`, and `VerdictEvent`; added "Dependencies (vendored in `lib/`)" section calling out the org.json 20240303 runtime dependency (was missing); bumped the output filename from `1.0.0.jar` to `1.0.2.jar`; added `lib/json-20240303.jar` to the test-only run commands so `RedRobinEventStreamTest` compiles + runs. Spec at `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` got a §13 "Post-spec additions (2026-05-17 → 2026-05-24)" audit appendix listing the shipped delta (EventStream + VerdictEvent + org.json + ulid + version bumps) with commit SHAs — spec body left intact since it's a historical design doc.

### #110 — re-enable heartbeat fork via lambda() closure capture (RESOLVED 2026-05-24 commit `<T1 SHA>`)
Plan F T1 closed as SUPERSEDED. Empirical evidence from T14 smoke 2026-05-23 (documented in operator's vault memory `feedback_sleep_fork_async_pattern.md`): Sleep `fork(lambda(...))` does NOT propagate captured globals into the spawned ScriptInstance — the bound symbol is null inside the fork's scope. The proper pattern is `on heartbeat_5s` (main script thread, full global scope access), now established at `adapters/cobaltstrike/redrobin.cna:475-486` as the Layer B verdict drain. The disabled fork block at `redrobin.cna:434-458` retains an inline rationale for future operators. Heartbeat fork was disabled in commit `b7c7c69` (the `#134` fix — which references `#110` directly in its commit message: *"its resolution path is now empirically clear: same lesson — use heartbeat_Xs hooks, not fork(), for any Sleep code that needs access to globals or persistent Java references"*) and never needs to be re-enabled — its original purpose (daemon-liveness probe every 30s) is adequately covered by `redrobin_http_post`'s inline warn-on-failure error reporting (`redrobin.cna:88-91`).

### #112 — reconcile CS-version-to-Java-version claim across 3 sources (VERIFY in Plan F)
**Source:** Pre-existing.
**Symptom:** Three sources claim slightly different mappings between CS version and bundled Java version (operator-setup.md, the JAR helper spec, and the original audit doc). Inconsistency confuses operators choosing their CS bundle.
**Files:** `docs/operator-setup.md`, possibly the JAR helper README + spec.
**Action in Plan F:** Verify against Fortra's actual CS release notes for v4.12, pick the correct claim, propagate to all 3 sources. If already reconciled, mark CLOSED.

---

## Deferred — post-Round 4 work (Rounds 5-7, schedule after demo)

These items are real but were deliberately scoped OUT of the current sprint (2026-05-24) because they require their own focused session and don't block the DEF CON CFP demo. Each is sized at ~1-3 hours and benefits from a clean workspace.

### Round 5 — v0.1.x refactors (~2-3h)

#### #91 — v0.1.x: BOM-tolerant rules loader
**Source:** Pre-existing (filed before current session).
**Symptom:** YAML rules files with a UTF-8 BOM (`﻿` at file start) fail to parse cleanly. Operators editing rules in Notepad or VSCode may unknowingly add a BOM.
**Files:** `redrobin/application/rules_loader.py` (or whichever module loads YAML rules — verify before fix).
**Fix:** Strip BOM at read time, OR open with `encoding="utf-8-sig"` which auto-strips. Test against a BOM'd fixture.

#### #109 — v0.1.x followup: handshake retry — distinguish network vs response-shape failure
**Source:** Pre-existing.
**Symptom:** `.cna`'s `redrobin_handshake_with_retry` treats all failure modes equally (network refused, daemon returned 4xx, bad JSON shape). Retry behavior should differentiate: retry on network errors, fail-fast on response-shape errors.
**Files:** `adapters/cobaltstrike/redrobin.cna` (handshake retry block).
**Fix:** Categorize the catch errors by class (ConnectException / HttpException / parse error), retry only network-class, log and stop on response-shape errors.

#### #114 — v0.1.x followup: migrate `adapters/cobaltstrike/spike/` scripts to HttpClient
**Source:** Pre-existing.
**Symptom:** Spike (experimental) `.cna` scripts in `adapters/cobaltstrike/spike/` still use the legacy direct `import java.net.*` + URLConnection pattern. Production code migrated to RedRobinHttp helper JAR in Task #115; spikes never followed.
**Files:** `adapters/cobaltstrike/spike/*.cna`.
**Fix:** Refactor each spike to use `[$REDROBIN_HTTP post: ...]` pattern. Low priority — spikes are experimental and don't ship.

### Round 6 — v0.2 architectural (~4-6h)

#### #126 — `beacon_id` vs `bid` naming inconsistency across protocol schemas
**Source:** T4 code review 2026-05-20
**Symptom:** `opsec_verdict.schema.json` uses `beacon_id` (Daemon code path emits this). Sibling event schemas (`beacon_command_output`, `beacon_command_input`, `beacon_initial`, `credential_observed`, `host_observation`) all use `bid` (3 letters). Protocol-wide naming is split across event types.
**Files:** `protocol/schemas/v0.1/events/*.schema.json` + Daemon emitters.
**Fix:** Pick one canonical name (probably `bid` for terseness, but `beacon_id` is more self-documenting). Migrate the other side. Touches schema files + Daemon publishers + JAR parser (`VerdictEvent.beaconId`).

#### #135 — two `Engagement` classes with disjoint YAML schemas
**Source:** B2 code review 2026-05-24
**Symptom:** Two distinct classes both named `Engagement` coexist:
  1. `redrobin.domain.engagement.Engagement` — used by `EngagementService` (CLI). Fields: `.id`, `.client`. Writes YAML with `id`, `client`, `scope.in_scope`, `objectives`.
  2. `redrobin.application.engagement_loader.Engagement` — used by daemon lifespan. Fields: `.slug`, `.name`, `.in_scope`, `.out_of_scope`, `.summary`. Reads YAML with `slug`, `name`, top-level `in_scope`.
Result: `redrobin engagement new X` writes a YAML the loader can't fully consume — daemon loads the engagement with empty `in_scope`/`out_of_scope`/`summary` regardless of what the operator put in the file.
**Files:** Both `Engagement` classes + `EngagementLoader` + `EngagementService.new()` template.
**Fix:** Unify on one `Engagement` class with one YAML schema. Either migrate the writer (`EngagementService.new()` template) or the loader. Touch test fixtures that assume the current shape. Includes a `redrobin migrate engagement-yaml` step if pre-2026-05-24 YAML files need to be normalized.

#### #113 — v0.2 followup: migrate `fork{}` async paths to `HttpClient.sendAsync()`
**Source:** Pre-existing.
**Symptom:** Several `.cna` paths still use Sleep `fork({...})` for async HTTP (now confirmed broken due to scope isolation, #134 + #110). The proper v0.2 migration is to push async to the Java side via `HttpClient.sendAsync()` rather than juggling Sleep forks.
**Files:** `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` (add `postAsync` returning CompletableFuture); affected `.cna` paths.
**Fix:** Bigger architectural change. Move all async dispatch from Sleep fork() to Java ExecutorService-backed `HttpClient.sendAsync()`. Sleep callers become synchronous (or use Aggressor's `on heartbeat_Xs` pattern for polling, as already established in #134 fix).

### Round 7 — infrastructure (~1h)

#### #100 — CS REST API bring-up in Ansible role
**Source:** Pre-existing.
**Symptom:** `csrestapi` dies on droplet reboot (verified empirically multiple times during smoke 2026-05-22+). Currently manually respawned via SSH; should be systemd-unit-managed and provisioned by the Red Team Playground Ansible role.
**Files:** `red-team-playground/ansible/roles/cobaltstrike-teamserver/` (verify path — outside this repo).
**Fix:** Systemd unit file template + role task that installs+enables it. Tested via `terraform destroy && terraform apply` cycle.

---

---

## Status of related closed tasks (for reference)

- **#123** — v0.1.x BLOCKER: wire .cna to receive opsec.verdict (Layer B) events. **CLOSED** 2026-05-24 — keystone TC1+TC2+TC3+TC4 all pass.
- **#128** — T14 operator-run smoke gate. **CLOSED** 2026-05-24 — full smoke validation passed.
- **#132** — reducer no-ops when no engagement set. **CLOSED** by B2 (`8e2d280`).
- **#133** — bare-args alias guards. **CLOSED** 2026-05-24 (`b3ed579`) — 7-case smoke verified.
- **#134** — `$REDROBIN_HTTP null` in hooks. **CLOSED** 2026-05-24 (`b7c7c69`) — fork removed.
- **#136** — clarify single-root episodic.db architecture. **CLOSED** — superseded by B2 implementation of per-engagement persistence + B3 docs.
- **#138** — path-3 schema compliance. **CLOSED** 2026-05-24 (`006e83a`→`cf37d36`) — 5-commit sequence shipped ULID + RFC3339 + emit-sub refactor.

---

## How to read this file

Each entry has:
- A `#NNN` task ID from the in-session tracker (may not match GitHub issues if filed separately).
- **Source**: the commit, code review, or smoke session that surfaced the bug.
- **Symptom**: what an operator or test sees.
- **Files**: pointers into the codebase.
- **Fix**: recommended scope, ranked toward cheapest viable path.

When picking the next slice: do **Critical for demo** first, then v0.1.x hardening, then v0.2 cleanup. Each item is sized to be a single atomic commit unless explicitly marked otherwise.
