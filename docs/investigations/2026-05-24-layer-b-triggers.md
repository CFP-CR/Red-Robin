# Layer B Trigger Investigation (closes #130)

**Date:** 2026-05-24
**Source incident:** T14 smoke 2026-05-22 — operator-run
**Backlog item:** #130 (Layer B fires even when Layer A is silent)
**HEAD at investigation:** 331a3a7 (dev-juan)

## Summary

Both hypotheses from #130 are false on the code at HEAD `331a3a7`. Hypothesis A (the preamble's "novel pattern" print is reason-text-gated) is structurally impossible: the conditional at `adapters/cobaltstrike/redrobin.cna:228` is action-equality only — `if ($verdict['action'] eq "ESCALATE")` — and the gate hash hard-codes `reason => "Layer B reviewing"` for the ESCALATE branch (`redrobin.cna:209`), so the daemon's actual reason text is never read into the preamble decision. Hypothesis B (Layer B has trigger paths beyond ESCALATE) is structurally false: `_run_layer_b` has exactly one caller (`redrobin/application/verdict_pipeline.py:139`), gated on `verdict.action == VerdictAction.ESCALATE` (line 134); the `/v1/events` route, the working-memory reducer, and the WS handlers never invoke Layer B. The most likely actual root cause (Hypothesis C, new in this report) is a client-side HTTP timeout on the gate POST: the JAR helper enforces a 200ms cap (`RedRobinHttp.java:30,37`), and on a cold daemon or under load the response can miss that window. The daemon-side Layer B is scheduled BEFORE the response returns (`verdict_pipeline.py:134-147`), so it still fires regardless of the client-side outcome; the retroactive WARN then arrives ~10s later via the WS event stream (`redrobin.cna:475-486 on heartbeat_5s`), independent of the gate POST. Recommended fix is a ~5 LOC `.cna` change to surface gate degradation to the beacon console; deferred to a future Plan G.

## Layer B call sites (complete enumeration)

Layer B can fire from EXACTLY ONE code path. Verified by grep across `redrobin/` for `LayerBEngine`, `_run_layer_b`, and `layer_b.evaluate` — no other invocations exist (2026-05-24 search).

The full chain:

1. **HTTP entry:** `redrobin/api/verdicts_router.py:126-142` — FastAPI route `POST /v1/verdict/quick` delegates to `pipeline.quick_verdict()`.
2. **Pipeline orchestration:** `redrobin/application/verdict_pipeline.py:77-149` — `quick_verdict()` runs scope check + Layer A synchronously, then at line 134 checks `if verdict.action == VerdictAction.ESCALATE`.
3. **Layer B scheduling:** `verdict_pipeline.py:139-147` — `self._task_factory(self._run_layer_b(...))` creates a tracked asyncio task; the route returns immediately with the Layer A verdict.
4. **Layer B execution:** `verdict_pipeline.py:151-191` — `_run_layer_b` awaits `self._layer_b.evaluate(...)` (line 162), then on WARN/BLOCK fires the retroactive callback (line 175) and bus publish (line 185).
5. **Bus emission:** `verdict_pipeline.py:193-223` — `_on_retroactive` publishes `{"type": "opsec.verdict", "payload": {..., "source": "layer_b", "previous_verdict": original_action.value, ...}}` onto the EventBus.
6. **Adapter delivery:** `redrobin/api/adapter_events_ws.py:35-42` — the adapter WebSocket endpoint subscribes to the bus and streams each event to the JAR's `RedRobinEventStream`. The `.cna`'s `on heartbeat_5s` (`redrobin.cna:475-486`) polls the JAR queue and surfaces the Layer B verdict via `blog([$evt beaconId], "[Red Robin] Layer B " . [$evt verdict] . ": " . [$evt reason])`.

### Paths that do NOT call Layer B (verified)

- `POST /v1/events` ingestion (`redrobin/api/events_router.py:53-93`): republishes the wire envelope onto the in-process EventBus via `_envelope_to_bus_event` (lines 35-45). No Layer B call.
- `POST /v1/events/replay` (`events_router.py:96-120`): same — bus publish only.
- `WorkingMemoryReducer._dispatch` (`redrobin/application/working_memory_reducer.py:107-117`): handles `beacon.command_output`, `beacon.initial`, `opsec.verdict`. No Layer B invocation.
- TUI WS handler (`redrobin/api/tui_ws.py`) and adapter WS handler (`redrobin/api/adapter_events_ws.py`): subscribe-only — they consume bus events, they do not produce verdicts.

**Hypothesis B from #130 is structurally false.** Layer B fires only when Layer A returns ESCALATE.

## The "novel pattern" preamble print

The backlog entry cites `adapters/cobaltstrike/aliases/tier1a_gate.sleep` for the preamble logic — that is wrong. `tier1a_gate.sleep` contains only alias bodies plus the `redrobin_alias_parse` / `redrobin_alias_require_args` helpers; full-file grep 2026-05-24 returns zero matches for `novel pattern` or `Layer B reviewing async`. The actual print lives in `adapters/cobaltstrike/redrobin.cna:229`, emitted by the central `redrobin_gate` sub (lines 217-232).

The exact conditional:

```sleep
# redrobin.cna lines 228-230
if ($verdict['action'] eq "ESCALATE") {
    blog($bid, "[Red Robin] novel pattern — Layer B reviewing async");
}
```

The upstream `redrobin_query_quick` (`redrobin.cna:181-213`):

- POSTs `/v1/verdict/quick` (line 192). On client-side timeout / connect error / non-2xx with empty body, returns `""`.
- Walks `hasmatch` checks for `"action":"BLOCK"`, `"action":"WARN"`, `"action":"ESCALATE"` in that order (lines 198-208).
- ESCALATE branch (lines 208-209) hard-codes `reason => "Layer B reviewing"` — the daemon's actual `reason` field is NEVER read into the verdict hash for the ESCALATE case.
- Else branch (lines 210-212) returns `%(action => "PASS", reason => "")` — the fail-open default.

**Hypothesis A from #130 is structurally impossible.** The preamble's conditional is action-equality only, not reason-text-dependent. The gate never even reads the daemon's reason text on ESCALATE.

## Walking the control flow for `shell ipconfig` and `shell tasklist /v`

Both commands share the same path; the walkthrough below covers both.

1. **Beacon-side dispatch:** Operator types `shell ipconfig` (or `shell tasklist /v`). CS's command parser routes to the `alias shell` override at `adapters/cobaltstrike/aliases/tier1b_wrapper.sleep:7-15`.
2. **Alias body:** Calls `redrobin_alias_parse("shell", @_)` → `%(args => "ipconfig", raw => "shell ipconfig")`. Calls `redrobin_alias_require_args` (passes — args non-empty). Calls `redrobin_gate($1, "shell", "shell ipconfig")`.
3. **Gate POST:** `redrobin_gate` (`redrobin.cna:217-232`) calls `redrobin_query_quick`. `redrobin_query_quick` (`redrobin.cna:181-213`) builds the JSON body with `command="shell"`, `raw="shell ipconfig"`, optional `integrity_level`, and POSTs to `/v1/verdict/quick`.
4. **Daemon Layer A evaluation:**
   - `verdicts_router.quick` (`verdicts_router.py:127-169`) calls `pipeline.quick_verdict(command="shell", raw="shell ipconfig", ...)`.
   - Pipeline scope check passes (default wildcard scope per `verdict_pipeline_factory.py:169`).
   - `layer_a.evaluate(command="shell", args="ipconfig", ...)` runs. `command in self._passthrough` is False — `shell` is not in `DEFAULT_PASSTHROUGH` at `verdict_pipeline_factory.py:47-59`. (The on-disk `rules/passthrough.yaml` is NOT loaded — `redrobin/infrastructure/rules_loader.py:258-259` explicitly skips it: `if rule_path.name == "passthrough.yaml": continue`.)
   - Walks rules. The four `command: shell` rules (`rules/shared/domain_admin_enumeration.yaml`, `nbtstat_arp_sweep.yaml`, `winrm_enable.yaml`, `vssadmin_list_shadows.yaml`) each have an `arg_pattern` regex (`net group "Domain Admins"`, `nbtstat -an|arp -a`, `winrm quickconfig|Enable-PSRemoting`, `vssadmin list shadows`). None match `ipconfig` or `tasklist /v`.
   - Returns `Verdict(action=VerdictAction.ESCALATE, reason="novel pattern", rule_id=None)` (`layer_a_engine.py:67`).
5. **Layer B scheduling (synchronous side effect):** Back in `quick_verdict` at `verdict_pipeline.py:134-147`, the task factory schedules `_run_layer_b` BEFORE the response returns.
6. **Bus emit + HTTP response:** Layer A verdict is published to the bus (`verdict_pipeline.py:127-132` → `_publish_layer_a_event`). Then `quick_verdict` returns the Verdict. The route handler builds `QuickVerdictResponse(action="ESCALATE", reason="novel pattern", rule_id=None, suggestions=[])` and FastAPI writes the HTTP response.
7. **Race:** This is the critical step. The daemon's Layer A round trip (scope check + rule walk + bus publish + response serialization) has to complete within 200ms or the JAR's `RedRobinHttp.post()` (`RedRobinHttp.java:37`) throws an `HttpTimeoutException`. Under cold start (first verdict after daemon boot, KB index loading, Anthropic client warmup) or under load (Layer B Anthropic call queued behind the response), 200ms is tight.
8. **Two outcomes depending on the race:**
   - **(a) Response wins the race:** `.cna` gets `{"action":"ESCALATE", "reason":"novel pattern", ...}`. `redrobin_query_quick` matches the ESCALATE branch (`redrobin.cna:208`) and returns `%(action=>"ESCALATE", reason=>"Layer B reviewing")`. `redrobin_gate` fires `blog($bid, "[Red Robin] novel pattern — Layer B reviewing async")`. Operator sees the preamble.
   - **(b) Timeout wins the race:** JAR throws `HttpTimeoutException` (or `HttpException` on 5xx). `redrobin_http_post` (`redrobin.cna:84-98`) catches, emits a `warn(...)` line to the **Script Console** (not the beacon console — Sleep's `warn` writes to the Script Console pane), and returns `""`. `redrobin_query_quick` falls through to PASS (`redrobin.cna:210-212`). `redrobin_gate` prints nothing — the gate (lines 217-232) only prints on BLOCK / WARN / ESCALATE; PASS is silent. Operator sees nothing in the beacon console.
9. **Layer B retroactive arrival (~10s later):** Independent of the race, Layer B was already scheduled in step 5. It runs `LayerBEngine.evaluate` (`layer_b_engine.py:118-189`), retrieves KB chunks, calls Anthropic, parses the JSON verdict (e.g., WARN with cmd.exe-spawn reason — see #129). The retroactive verdict is published to the bus by `_run_layer_b` → `_on_retroactive` (`verdict_pipeline.py:193-223`). The adapter WS streams the event to the JAR; `on heartbeat_5s` (`redrobin.cna:475-486`) drains the queue on the next 5s tick and emits `blog($bid, "[Red Robin] Layer B WARN: <reason>")`. Operator sees the WARN line.

**The observed symptom (no preamble + Layer B WARN ~10s later) is the textbook outcome of branch (b) — the gate POST hit the 200ms client timeout while the daemon-side Layer B continued running asynchronously.**

## Verdict on the #130 hypotheses

| Hypothesis | Status | Why |
|---|---|---|
| A: Layer A returned ESCALATE with a different `reason` text and the preamble's filter swallowed it | **FALSE** | Preamble conditional is action-equality only (`redrobin.cna:228`). Reason text never enters the print decision. The gate hash hard-codes `reason="Layer B reviewing"` for the ESCALATE branch (`redrobin.cna:209`). |
| B: Layer B has a trigger path beyond ESCALATE (telemetry event subscription, periodic sweep, etc.) | **FALSE** | Grep across `redrobin/` finds exactly one caller of `_run_layer_b` (`verdict_pipeline.py:139`), exactly one caller of `layer_b.evaluate` (`verdict_pipeline.py:162`), both gated on `verdict.action == VerdictAction.ESCALATE`. The `/v1/events` route never calls Layer B; the reducer never calls Layer B. |
| C (new): Gate POST hit the 200ms client timeout; Layer B still fired daemon-side | **MOST LIKELY** | Matches the observed timing (Layer B WARN ~10s later via the WS event stream is independent of the gate). The daemon-side `_run_layer_b` is scheduled before the response returns (`verdict_pipeline.py:134-147`), so a client-side timeout does not cancel Layer B. |

Hypothesis C can be verified post-hoc by inspecting the per-engagement `episodic.db` for Layer A verdict rows around the T14 timestamps. If two ESCALATE rows exist for `shell ipconfig` + `shell tasklist /v` followed by Layer B WARN rows, the daemon completed its work — the gap was on the client side. The Appendix below has the exact SQL probe.

## Recommended fix (defer to Plan G)

A ~5 LOC `.cna` change in `redrobin.cna` to surface gate degradation to the beacon console. Cleanest approach: have `redrobin_query_quick` return a third hash key (e.g., `"degraded" => true`) when it hit the catch-block (timeout / connect refused / non-2xx). `redrobin_gate` then on `$verdict['degraded']` emits an informational `blog($bid, ...)` so operators understand why Layer B WARNs appear without preambles.

Concrete file:line patch sketch (DO NOT IMPLEMENT in this report):

```sleep
# redrobin.cna lines 192-212 — add a degraded marker in the catch path
$response = redrobin_http_post("/v1/verdict/quick", $body);
if (strlen($response) == 0) {
    return %(action => "PASS", reason => "", degraded => true);
}
# ... existing hasmatch chain (lines 198-212) ...

# redrobin.cna lines 217-232 — surface the degraded marker
sub redrobin_gate {
    # ... existing body (lines 218-227) ...
    if ($verdict['degraded']) {
        blog($bid, "[Red Robin] gate degraded (daemon timeout >200ms) — Layer B WARN may still arrive via WS");
    }
    return true;
}
```

**Risk:** low. The check is a hash-key probe (Sleep boolean). The `blog` is informational. No production code path changes its behavior — PASS is still returned, the command still dispatches. The only new behavior is operator-visible attribution when Layer B WARN arrives without a preamble.

**Defer to a future Plan G.** It is out of scope for Plan D (audit-only). It is also low priority: the demo runs on a warm daemon where the 200ms timeout is comfortable.

Alternative scoping (operator's choice in Plan G):
- **Smaller:** bump the JAR's POST timeout from 200ms to 500ms in `RedRobinHttp.java:37` if the operator can tolerate longer worst-case beacon-console blocking. Closes the race window without UX changes. But: the 200ms cap is intentional per spec §1.2.1 (alias bodies must not block the operator visibly).
- **Larger:** add a structured "verdict pending" line to the beacon console on every gate degradation. Requires changing `redrobin_query_quick`'s return shape, the catch-path, AND a smoke test. Sized at ~30 LOC + a smoke probe.

## Implications for Plan E (#129 — Layer B prompt tuning)

**Plan E's scope does NOT need to expand.** The investigation rules out Hypothesis B (no additional Layer B trigger paths), so Plan E continues to address only the canonical ESCALATE → Layer B flow.

Specifically:
- Plan E's prompt-tuning target stays at `redrobin/application/layer_b_engine.py:SYSTEM_PROMPT` (lines 42-64).
- Plan E does NOT need to add new trigger filtering (e.g., "skip Layer B for low-risk shell args") because Layer B already fires only on Layer A ESCALATE.
- The behavior #129 actually wants (Layer B should default toward PASS for benign recon like `ipconfig` / `tasklist /v`) is purely a prompt-engineering problem. The existing prompt invites the LLM to surface ANY detection risk; #129 wants the LLM to weigh detection cost against actual operator intent. Pure prompt change.
- Plan E may OPTIONALLY also implement the Plan G fix above as a tiny addendum if scope allows, since both touch operator-visible UX around Layer B. But scope-wise they are independent.

## Appendix: SQL probe for the T14 timeframe (optional)

A copy-pasteable SQL probe for the operator to run optionally. The probe surfaces Layer A + Layer B rows for the smoke engagement around 2026-05-22:

```powershell
# Per-engagement episodic.db — replace <slug> with the active engagement
# slug from T14 (likely tc4-smoke per #138 smoke notes).
python -c "import sqlite3,sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect(r'C:\Users\jcspi\.redrobin\engagements\<slug>\episodic.db'); rows=c.execute(\"SELECT id,command,verdict,substr(reason,1,80),created_at FROM episodes WHERE command LIKE 'shell %' AND created_at LIKE '2026-05-22%' ORDER BY id\").fetchall(); [print(r) for r in rows]"
```

Expected output IF Hypothesis C is correct (assuming the smoke session's episodic.db is preserved):
- Layer A row for each of `shell ipconfig` / `shell tasklist /v` with `verdict='ESCALATE'`, `reason='novel pattern'` — proves the daemon DID return ESCALATE.
- Layer B retroactive row for each with `verdict='WARN'`, reason about cmd.exe child process.

Also useful as a confirmation: paste the daemon's stdout for the T14 window into a probe for the lines `POST /v1/verdict/quick HTTP/1.1" 200 OK` and `POST /v1/events`. Two `200 OK` rows for each `shell` command (one verdict, one input event) confirm the daemon completed its work; the silence on the `.cna` side then implicates the client-side timeout.

If the operator does not have access to the T14 episodic.db (it was rotated or the engagement was destroyed), the investigation's conclusion stands on the code reading alone (Hypothesis B is structurally false; Hypothesis A is structurally impossible; Hypothesis C is the only consistent explanation given the architecture).

## Closing note

Backlog entry #130 was filed with two incorrect file:line citations: it pointed at `tier1a_gate.sleep` for the preamble logic (the actual location is `redrobin.cna:229`), and it described the preamble's conditional as reason-text-gated (the actual conditional is action-equality only). Future operators reading the backlog after this investigation should treat #130's hypothesis framing as historical context; the actual story is captured here. Status flipped to RESOLVED in `docs/superpowers/backlog.md` Plan D close-out (Task 2).
