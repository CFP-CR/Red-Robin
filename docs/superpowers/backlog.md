# Red Robin — Backlog (v0.1.x → v0.2)

Generated 2026-05-24 from in-session task tracker during the adapter event-stream + engagement-isolation work (commits `ec5dbe5` → `d949404`). These items were discovered while shipping the keystone v0.1 BLOCKER (#123) but were deliberately scoped out of the shipping commits to keep them atomic.

Each entry below includes the bug source (commit / smoke session), reproduction notes, and recommended fix scope. None of these block the DEF CON RTV CFP demo (2026-05-31) unless explicitly marked **Critical for demo**.

---

## Critical for demo — fix before 2026-05-31

These materially affect the live demo experience or shipping correctness.

### #133 — bare `mimikatz` alias fails dispatch (`&bmimikatz` requires subcommand)
**Source:** T14 smoke 2026-05-22, Script Console showed `[!] Function call &bmimikatz failed: argument command is missing at tier1a_gate.sleep:49`
**Symptom:** Operator types `mimikatz` (no subcommand). Layer A "novel pattern" line prints, Layer B BLOCK verdict surfaces, BUT the beacon never actually receives the mimikatz task. The dispatch failed before reaching the beacon. UX looks like it worked (BLOCK shown), but the command never executed.
**Files:** `adapters/cobaltstrike/aliases/tier1a_gate.sleep:48-50` (`&bmimikatz($1, %p['args'])` with empty `%p['args']`).
**Fix:** When args is empty, either pass a sensible default (`logonpasswords`) or short-circuit before calling `&bmimikatz`. Probably the safer move is: if args empty, BLOCK preemptively with a clear "[Red Robin] mimikatz requires a subcommand — use e.g. `mimikatz logonpasswords`" message.
**Scope:** ~10-20 LOC in the alias + a smoke test case for bare `mimikatz`.

### #134 — `$REDROBIN_HTTP null` at `redrobin.cna:83` when called from `BEACON_OUTPUT` / `on beacon_tasked` hooks
**Source:** T14 smoke 2026-05-22, Script Console errors `[!] Attempted to call a non-static method on a null reference at redrobin.cna:83` firing 2s after `blog()` to beacon.
**Symptom:** `sub redrobin_http_post` body has `[$REDROBIN_HTTP post: $path, $body]`. When called from `set BEACON_OUTPUT { redrobin_push_event_async(...); }` or `on beacon_tasked { redrobin_push_event_async(...); }`, `$REDROBIN_HTTP` resolves to `null`. Same scope-isolation flavor as the fork() issue (#110) but in Aggressor hook context. Net effect: path-3 observation events (`beacon.command_input` / `beacon.command_output`) NEVER reach the daemon, so working memory + episodic.db don't see beacon outputs.
**Files:** `adapters/cobaltstrike/redrobin.cna:83` (HTTP post line), plus the hook bodies at `~308-318`.
**Fix candidates:**
  1. Mirror the `on heartbeat_5s` workaround: queue events into a Java-side static queue, drain on a Sleep heartbeat tick that has access to globals.
  2. Re-declare `global('$REDROBIN_HTTP $REDROBIN_URL $REDROBIN_TOKEN');` inside the hook bodies explicitly (cheapest if it works; verify empirically).
  3. Cache the JAR instance in a Java static accessor (`RedRobinHttp.getInstance()`) and have the hook bodies call that.
**Scope:** Try #2 first (1-line fix each in `set BEACON_OUTPUT` + `on beacon_tasked` bodies). If that doesn't propagate, fall back to #1.

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
