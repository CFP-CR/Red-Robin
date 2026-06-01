# Plan D — Layer B trigger investigation (closes #130)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with Opus 4.7. Single atomic commit for the investigation report (T1) plus one atomic commit for the backlog close-out (T2).

**Goal:** Trace why `shell ipconfig` and `shell tasklist /v` returned Layer B WARN ~10s after dispatch during the T14 smoke 2026-05-22 without surfacing the `[Red Robin] novel pattern — Layer B reviewing async` preamble first. Produce a written audit at `docs/investigations/2026-05-24-layer-b-triggers.md` that maps every Layer B trigger path, identifies the exact conditional gating the preamble print, picks the winning hypothesis (A or B from #130's framing), and names a concrete one-line fix sized for a follow-on plan. No code changes in this plan — investigation only.

**Architecture / approach:** Read-only audit. The deliverable is one new markdown file under a new `docs/investigations/` directory. No Python, no Sleep, no JAR, no schema, no tests touched. Plan E (`#129`) consumes the report's "Implications for Plan E" section to decide whether its Layer B prompt-tuning scope needs to expand. If the investigation surfaces a one-line fix, it is named in the report and deferred to a future Plan G; this plan does not implement it.

**Tech Stack:** Markdown only. A short SQLite probe snippet is included in an "If you want to verify against live data" appendix for the operator to run optionally — no code change accompanies it.

**Verified facts driving this plan (no assumptions; every claim was read directly from the dev-juan tree at HEAD `331a3a7` on 2026-05-24):**

1. **The "novel pattern" preamble print lives at `adapters/cobaltstrike/redrobin.cna:229`, NOT in `tier1a_gate.sleep` as the backlog entry claims.** The backlog entry for #130 mis-cites the file. The preamble is emitted by `redrobin_gate` (defined at `redrobin.cna:217-232`). `tier1a_gate.sleep` contains alias bodies + the `redrobin_alias_parse` / `redrobin_alias_require_args` helpers; it does not contain any `blog`/`println` call referencing the novel-pattern string (verified by full read of `tier1a_gate.sleep` 2026-05-24 — 222 lines, zero matches for `novel pattern` or `Layer B reviewing async`).

2. **The preamble's gating conditional is NOT reason-text-dependent.** `redrobin.cna:228` reads `if ($verdict['action'] eq "ESCALATE") { blog($bid, "[Red Robin] novel pattern — Layer B reviewing async"); }`. The hash returned by `redrobin_query_quick` for the ESCALATE branch (`redrobin.cna:208-209`) hard-codes `reason => "Layer B reviewing"` — the gate never reads the daemon's actual reason text for the print decision. So Hypothesis A from #130 ("reason text doesn't match the preamble filter") is structurally impossible given the current `.cna`.

3. **Layer B fires from EXACTLY ONE call site: `VerdictPipeline._run_layer_b` at `redrobin/application/verdict_pipeline.py:151`, scheduled from `quick_verdict` at line 139 gated by `if verdict.action == VerdictAction.ESCALATE` (line 134).** Grep across `redrobin/` for `_run_layer_b`, `LayerBEngine`, and `layer_b.evaluate` confirms zero other invocations (2026-05-24 search results — only the one factory wiring + pipeline call). The `events_router.py` POST `/v1/events` handler republishes the wire envelope onto the in-process EventBus (`redrobin/api/events_router.py:91`) but never calls Layer B. The `WorkingMemoryReducer._dispatch` (`redrobin/application/working_memory_reducer.py:107-117`) only handles `beacon.command_output`, `beacon.initial`, and `opsec.verdict` — it never invokes Layer B. **Hypothesis B from #130 ("Layer B has a trigger path beyond ESCALATE") is structurally false.**

4. **Layer A returns ESCALATE for `shell ipconfig` and `shell tasklist /v`.** `redrobin/application/layer_a_engine.py:48-67`: the engine first checks `command in self._passthrough`. The active passthrough set is `DEFAULT_PASSTHROUGH` from `verdict_pipeline_factory.py:47-59` — does not contain `shell`. (The rules-dir `passthrough.yaml` at `rules/passthrough.yaml` IS NOT loaded — `rules_loader.py:258-259` explicitly skips it: `if rule_path.name == "passthrough.yaml": continue`. So the DEFAULT_PASSTHROUGH hardcoded list is the only source of truth.) With no passthrough hit, the engine walks rules in order; the four `command: shell` rules (`domain_admin_enumeration`, `nbtstat_arp_sweep`, `winrm_enable`, `vssadmin_list_shadows`) have `arg_pattern`s that don't match `ipconfig` or `tasklist /v`. No rule matches → `Verdict(action=ESCALATE, reason="novel pattern", rule_id=None)` (line 67).

5. **The HTTP path between `.cna` and daemon has a 200ms read timeout enforced by the JAR helper (`adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java:30,37`: `connectTimeout(Duration.ofMillis(200))` + per-request `.timeout(Duration.ofMillis(200))`).** On client-side timeout, `redrobin_http_post` (`redrobin.cna:77-99`) catches the exception, prints a warn line (`[Red Robin] daemon timeout at <url> (>200ms) — slow network or overloaded daemon` — line 89), and returns `""`. `redrobin_query_quick` then falls through to its `else` PASS branch (`redrobin.cna:210-212` — `return %(action => "PASS", reason => "")`). `redrobin_gate` sees `$verdict['action'] eq "PASS"` and emits no preamble (lines 217-232 print only on BLOCK/WARN/ESCALATE). **This is the fail-open semantics the spec defines.**

6. **Layer B retroactive verdicts reach the beacon console via a SEPARATE channel from the gate — `redrobin.cna:475-486 (on heartbeat_5s)` polls `$REDROBIN_EVENT_STREAM` (the WebSocket-backed JAR) for `opsec.verdict` events with `[$evt source] eq "layer_b"` and emits `blog([$evt beaconId], "[Red Robin] Layer B " . [$evt verdict] . ": " . [$evt reason])`.** This path is fully independent of `redrobin_gate`. So Layer B WARN/BLOCK lines arrive even when the gate POST failed/timed out.

7. **Combining facts 1-6: the most likely root cause is a `/v1/verdict/quick` client-side timeout (or transient HTTP error) for the two T14 cases.** On the daemon side, Layer A still runs synchronously to ESCALATE and `_run_layer_b` is scheduled BEFORE `quick_verdict` returns (lines 134-147 of `verdict_pipeline.py` — task creation happens inside the response handler). So Layer B fires regardless of whether the client gets the response in time. The async retroactive WARN arrives ~10s later via the WS event stream — exactly matching the observed behavior. Under this hypothesis, the operator's beacon console would have shown either:
   - A `warn(...)` line in the Script Console (not the beacon console) about daemon timeout, OR
   - No warn at all if the JAR's `post()` returned a non-200 with empty body that didn't trigger the catch-block warn (e.g., a 504 from a slow daemon dependency).
   This is consistent with the operator noting "no preamble" without noting any visible warn line either — the warn is on a different console pane from the beacon output.

8. **Backlog entry #130 contains TWO factual errors and ONE valid open question.** Errors: (a) wrong file location for the preamble logic (tier1a_gate.sleep vs. redrobin.cna), (b) wrong characterization of the preamble's conditional (reason-text-dependent vs. action-equality only). The valid open question survives: "is there a Layer B trigger beyond ESCALATE?" — and the answer is **NO** (fact 3 above). The hypothesis A / B framing in the backlog is therefore a false dichotomy; the actual root cause is a third possibility (gate POST failed before response landed).

**Risk note:** Read-only audit. No source files are edited. No JAR rebuild. No daemon restart. The investigation report is a new markdown file in a new directory; no existing file structure changes. The only commit-time risk is that the report mis-cites a file:line — mitigated by every claim being re-verified against the dev-juan HEAD before the report is committed.

**Starting state assumption:** `dev-juan` HEAD = `331a3a7` (Plan C close-out — `docs(backlog): mark #116, #120, #111 RESOLVED -- Plan C close-out`). No outstanding diff. The investigations directory does NOT exist yet at HEAD; Task 1 creates it.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `docs/investigations/2026-05-24-layer-b-triggers.md` | Create | New investigation report — full audit of Layer B trigger paths, the preamble conditional, hypothesis verdict, recommended fix, and Plan E implications (#130) |
| `docs/superpowers/backlog.md` | Modify | Close out `#130` in entry heading + body; flip scope table row D to DONE with Task 1 commit SHA |

---

## Task 1: Write the investigation report

**Files:**
- Create: `docs/investigations/2026-05-24-layer-b-triggers.md`

The report's job is to give a future operator (or Plan E author) enough context to know exactly which Layer B trigger paths exist, why the T14 preamble was silent, and what to change if they want the preamble to fire reliably. The report should be terse but each claim must carry a file:line citation. Aim for ~150-220 lines of markdown.

- [ ] **Step 1: Create the investigations directory and write the report**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
mkdir -p docs/investigations
```

Then create `docs/investigations/2026-05-24-layer-b-triggers.md` with these sections (in order):

### Section: Header + frontmatter-style metadata block (~5 lines)

Title: `# Layer B Trigger Investigation (closes #130)`

Below the title, a 4-row metadata block:
- `**Date:** 2026-05-24`
- `**Source incident:** T14 smoke 2026-05-22 — operator-run`
- `**Backlog item:** #130 (Layer B fires even when Layer A is silent)`
- `**HEAD at investigation:** 331a3a7 (dev-juan)`

### Section: Summary (~5 lines, one paragraph)

State the verdict in one paragraph:
- Hypothesis A (preamble's reason-text filter) is FALSE: the preamble's conditional is action-equality only (`redrobin.cna:228`), not reason-text-dependent.
- Hypothesis B (Layer B has trigger paths beyond ESCALATE) is FALSE: `_run_layer_b` has exactly one caller (`verdict_pipeline.py:139`), gated on `verdict.action == ESCALATE` (line 134).
- The actual root cause is most likely a client-side HTTP timeout on `/v1/verdict/quick` (200ms hard cap in the JAR helper at `RedRobinHttp.java:30,37`): the daemon ran Layer A → ESCALATE and scheduled Layer B before the response landed in the .cna, so the gate degraded to silent PASS while Layer B still fired and surfaced via the WS event stream ~10s later.
- Recommendation: a one-line `.cna` improvement to also surface the gate-degradation case to the beacon console (so operators stop seeing "silent" Layer B WARNs). Deferred to a future Plan G — see "Recommended fix" section.

### Section: Trigger paths for Layer B (~25-40 lines)

Subsection header `## Layer B call sites (complete enumeration)`.

State up front: "Layer B can fire from EXACTLY ONE code path. Verified by grep across `redrobin/` for `LayerBEngine`, `_run_layer_b`, and `layer_b.evaluate` — no other invocations exist (2026-05-24 search)."

Then a numbered list of the call chain:
1. **HTTP entry:** `redrobin/api/verdicts_router.py:126-142` — FastAPI route `POST /v1/verdict/quick` delegates to `pipeline.quick_verdict()`.
2. **Pipeline orchestration:** `redrobin/application/verdict_pipeline.py:77-149` — `quick_verdict()` runs scope check + Layer A synchronously, then at line 134 checks `if verdict.action == VerdictAction.ESCALATE`.
3. **Layer B scheduling:** `verdict_pipeline.py:139-147` — `self._task_factory(self._run_layer_b(...))` creates a tracked asyncio task; the route returns immediately with the Layer A verdict.
4. **Layer B execution:** `verdict_pipeline.py:151-191` — `_run_layer_b` awaits `self._layer_b.evaluate(...)` (line 162), then on WARN/BLOCK fires the retroactive callback (line 175) and bus publish (line 185).
5. **Bus emission:** `verdict_pipeline.py:193-223` — `_on_retroactive` publishes `{"type": "opsec.verdict", "payload": {..., "source": "layer_b", "previous_verdict": original_action.value, ...}}` onto the EventBus.
6. **Adapter delivery:** `redrobin/api/adapter_events_ws.py:35-42` — the adapter WebSocket endpoint subscribes to the bus and streams each event to the JAR's `RedRobinEventStream`. The .cna's `on heartbeat_5s` (`redrobin.cna:475-486`) polls the JAR queue and surfaces the Layer B verdict via `blog([$evt beaconId], "[Red Robin] Layer B " . [$evt verdict] . ": " . [$evt reason])`.

Then a subsection `## Paths that do NOT call Layer B (verified)`:
- `POST /v1/events` (`redrobin/api/events_router.py:53-93`): republishes the wire envelope onto the bus via `_envelope_to_bus_event` (line 35-45). No Layer B call.
- `POST /v1/events/replay` (`events_router.py:96-120`): same — bus publish only.
- `WorkingMemoryReducer._dispatch` (`working_memory_reducer.py:107-117`): handles `beacon.command_output`, `beacon.initial`, `opsec.verdict`. No Layer B invocation.
- TUI / adapter WS handlers (`tui_ws.py`, `adapter_events_ws.py`): subscribe-only; never call into the pipeline.

**Conclusion of this section (one bold line):** **Hypothesis B from #130 is structurally false** — Layer B fires only when Layer A returns ESCALATE.

### Section: Preamble conditional (~15-25 lines)

Subsection header `## The "novel pattern" preamble print`.

Lead with the citation correction: "The backlog entry cites `adapters/cobaltstrike/aliases/tier1a_gate.sleep` for the preamble logic — that's wrong. `tier1a_gate.sleep` contains only alias bodies + `redrobin_alias_parse`/`redrobin_alias_require_args` helpers; full-file grep 2026-05-24 returns zero matches for `novel pattern` or `Layer B reviewing async`. The actual print lives in `adapters/cobaltstrike/redrobin.cna:229`, emitted by the central `redrobin_gate` sub (lines 217-232)."

Then quote the exact conditional from `redrobin.cna:228-230`:

```sleep
if ($verdict['action'] eq "ESCALATE") {
    blog($bid, "[Red Robin] novel pattern — Layer B reviewing async");
}
```

Walk through the upstream `redrobin_query_quick` (`redrobin.cna:181-213`):
- POSTs `/v1/verdict/quick` (line 192). On client-side timeout / connect error / non-2xx with empty body, returns `""`.
- Walks `hasmatch` checks for `"action":"BLOCK"`, `"action":"WARN"`, `"action":"ESCALATE"` in that order.
- ESCALATE branch (lines 208-209) hard-codes `reason => "Layer B reviewing"` — the daemon's actual `reason` field is NEVER read into the verdict hash for the ESCALATE case.
- Else branch (line 211) returns `%(action => "PASS", reason => "")` — the fail-open default.

**Conclusion of this section (one bold line):** **Hypothesis A from #130 is structurally impossible** — the preamble's conditional is action-equality only, not reason-text-dependent. The gate never even reads the daemon's reason text on ESCALATE.

### Section: Reproducing the T14 cases mentally (~30-50 lines)

Subsection header `## Walking the control flow for `shell ipconfig` and `shell tasklist /v`.`

Step through both commands identically (since they share a path):
1. **Beacon-side dispatch:** Operator types `shell ipconfig` (or `shell tasklist /v`). CS's command parser routes to the `alias shell` override defined in `adapters/cobaltstrike/aliases/tier1b_wrapper.sleep:7-15`.
2. **Alias body:** Calls `redrobin_alias_parse("shell", @_)` → returns `%(args => "ipconfig", raw => "shell ipconfig")` (or `tasklist /v` analogue). Calls `redrobin_alias_require_args` (passes since args is non-empty). Calls `redrobin_gate($1, "shell", "shell ipconfig")`.
3. **Gate POST:** `redrobin_gate` (`redrobin.cna:217-232`) calls `redrobin_query_quick`. `redrobin_query_quick` (`redrobin.cna:181-213`) builds the JSON body with `command="shell"`, `raw="shell ipconfig"`, optional `integrity_level`, and POSTs to `/v1/verdict/quick`.
4. **Daemon Layer A evaluation:**
   - `verdicts_router.quick` (`verdicts_router.py:127-169`) calls `pipeline.quick_verdict(command="shell", raw="shell ipconfig", ...)`.
   - Pipeline scope check passes (default wildcard scope per `verdict_pipeline_factory.py:169`).
   - `layer_a.evaluate(command="shell", args="ipconfig", ...)` runs. `command in self._passthrough` is False (`shell` is not in `DEFAULT_PASSTHROUGH` at `verdict_pipeline_factory.py:47-59`).
   - Walks rules. The four `command: shell` rules (`rules/shared/domain_admin_enumeration.yaml`, `nbtstat_arp_sweep.yaml`, `winrm_enable.yaml`, `vssadmin_list_shadows.yaml`) each have an `arg_pattern` regex (`net group "Domain Admins"`, `nbtstat -an|arp -a`, `winrm quickconfig|Enable-PSRemoting`, `vssadmin list shadows`). None match `ipconfig` or `tasklist /v`.
   - Returns `Verdict(action=VerdictAction.ESCALATE, reason="novel pattern", rule_id=None)` (`layer_a_engine.py:67`).
5. **Layer B scheduling (synchronous side effect):** Back in `quick_verdict` at `verdict_pipeline.py:134-147`, the task factory schedules `_run_layer_b` BEFORE the response returns.
6. **Bus emit + HTTP response:** Layer A verdict is published to the bus (`verdict_pipeline.py:127-132` → `_publish_layer_a_event`). Then `quick_verdict` returns the Verdict. The route handler builds `QuickVerdictResponse(action="ESCALATE", reason="novel pattern", rule_id=None, suggestions=[])` and FastAPI writes the HTTP response.
7. **Race:** This is the critical step. The daemon's Layer A round trip (scope check + rule walk + bus publish + response serialization) has to complete within 200ms or the JAR's `RedRobinHttp.post()` (`RedRobinHttp.java:37`) throws an `HttpTimeoutException`. Under cold start (first verdict after daemon boot, KB index loading, Anthropic client warmup) or under load (Layer B Anthropic call queued behind the response), 200ms is tight.
8. **Two outcomes depending on the race:**
   - **(a) Response wins the race:** `.cna` gets `{"action":"ESCALATE", "reason":"novel pattern", ...}`. `redrobin_query_quick` matches the ESCALATE branch (`redrobin.cna:208`) and returns `%(action=>"ESCALATE", reason=>"Layer B reviewing")`. `redrobin_gate` fires `blog($bid, "[Red Robin] novel pattern — Layer B reviewing async")`. Operator sees the preamble.
   - **(b) Timeout wins the race:** JAR throws `HttpTimeoutException` (or `HttpException` on 5xx). `redrobin_http_post` (`redrobin.cna:84-98`) catches, emits a `warn(...)` line to the **Script Console** (not the beacon console — `warn` in Sleep is a Script Console function), and returns `""`. `redrobin_query_quick` falls through to PASS (`redrobin.cna:210-212`). `redrobin_gate` prints nothing (PASS branch is silent — lines 217-232 only print on BLOCK/WARN/ESCALATE). Operator sees nothing in the beacon console.
9. **Layer B retroactive arrival (~10s later):** Independent of the race, Layer B was already scheduled in step 5. It runs `LayerBEngine.evaluate` (`layer_b_engine.py:118-189`), retrieves KB chunks, calls Anthropic, parses the JSON verdict (e.g., WARN with cmd.exe-spawn reason — see #129). The retroactive verdict is published to the bus by `_run_layer_b` → `_on_retroactive` (`verdict_pipeline.py:193-223`). The adapter WS streams the event to the JAR; `on heartbeat_5s` (`redrobin.cna:475-486`) drains the queue on the next 5s tick and emits `blog($bid, "[Red Robin] Layer B WARN: <reason>")`. Operator sees the WARN line.

**Conclusion of this section (one bold line):** **The observed symptom (no preamble + Layer B WARN ~10s later) is the textbook outcome of branch (b) — the gate POST hit the 200ms client timeout while the daemon-side Layer B continued running asynchronously.**

### Section: Hypothesis & evidence (~10-15 lines)

Subsection header `## Verdict on the #130 hypotheses`.

A tight table:

| Hypothesis | Status | Why |
|---|---|---|
| A: Layer A returned ESCALATE with a different `reason` text and the preamble's filter swallowed it | **FALSE** | Preamble conditional is action-equality only (`redrobin.cna:228`). Reason text never enters the print decision. The gate hash hard-codes `reason="Layer B reviewing"` for the ESCALATE branch (`redrobin.cna:209`). |
| B: Layer B has a trigger path beyond ESCALATE (telemetry event subscription, periodic sweep, etc.) | **FALSE** | Grep across `redrobin/` finds exactly one caller of `_run_layer_b` (`verdict_pipeline.py:139`), exactly one caller of `layer_b.evaluate` (`verdict_pipeline.py:162`), both gated on `verdict.action == ESCALATE`. The `/v1/events` route never calls Layer B; the reducer never calls Layer B. |
| C (new): Gate POST hit the 200ms client timeout; Layer B still fired daemon-side | **MOST LIKELY** | Matches the observed timing (Layer B WARN ~10s later via the WS event stream is independent of the gate). The daemon-side `_run_layer_b` is scheduled before the response returns (`verdict_pipeline.py:134-147`), so a client-side timeout does not cancel Layer B. |

State as a follow-up:
- Hypothesis C can be verified post-hoc by inspecting the per-engagement `episodic.db` for Layer A verdict rows around the T14 timestamps. If two ESCALATE rows exist for `shell ipconfig` + `shell tasklist /v` followed by Layer B WARN rows, the daemon completed its work — the gap was on the client side. The "If you want to verify against live data" appendix below has the exact SQL.

### Section: Recommended fix (~15-25 lines)

Subsection header `## Recommended fix (defer to Plan G)`.

State the fix as a 4-5 line change to `redrobin.cna:217-232`:
- Have `redrobin_gate` distinguish "PASS because Layer A said so" from "PASS because the daemon was unreachable / slow". The cleanest approach: have `redrobin_query_quick` return a third hash key (e.g., `"degraded" => true`) when it hit the catch-block (timeout / connect refused / non-2xx). `redrobin_gate` then on `$verdict['degraded']` emits `blog($bid, "[Red Robin] gate degraded (daemon timeout) — Layer B may still arrive via WS")` so operators understand why Layer B WARNs appear without preambles.

Concrete file:line patch sketch (DO NOT IMPLEMENT in this plan):

```sleep
# redrobin.cna lines 192-212 — add a degraded marker in the catch path
$response = redrobin_http_post("/v1/verdict/quick", $body);
if (strlen($response) == 0) {
    return %(action => "PASS", reason => "", degraded => true);
}
# ... existing hasmatch chain ...

# redrobin.cna lines 217-232 — surface the degraded marker
sub redrobin_gate {
    # ... existing body ...
    if ($verdict['degraded']) {
        blog($bid, "[Red Robin] gate degraded (daemon timeout >200ms) — Layer B WARN may still arrive via WS");
    }
    return true;
}
```

Risk: low. The check is a hash-key probe (Sleep boolean). The `blog` is informational. No production code path changes its behavior — PASS is still returned, the command still dispatches. The only new behavior is operator-visible attribution when Layer B WARN arrives without a preamble.

**Defer this fix to a future Plan G** — it is not in scope for Plan D (audit-only). It is also low priority: the demo runs on a warm daemon where the 200ms timeout is comfortable.

Alternative scoping (operator's choice in Plan G):
- **Smaller:** just bump the JAR's POST timeout from 200ms to 500ms in `RedRobinHttp.java:37` if the operator can tolerate longer worst-case beacon-console blocking. Closes the race window without UX changes. But: the 200ms cap is intentional per spec §1.2.1 (alias bodies must not block the operator visibly).
- **Larger:** add a structured "verdict pending" line to the beacon console on every gate degradation. Requires changing `redrobin_query_quick`'s return shape, the catch-path, AND a smoke test. Sized at ~30 LOC + a smoke probe.

### Section: Implications for Plan E (~10-15 lines)

Subsection header `## Implications for Plan E (#129 — Layer B prompt tuning)`.

State the answer clearly: **Plan E's scope does NOT need to expand**. The investigation rules out Hypothesis B (no additional Layer B trigger paths), so Plan E continues to address only the canonical ESCALATE → Layer B flow.

Specifically:
- Plan E's prompt-tuning target stays at `redrobin/application/layer_b_engine.py:SYSTEM_PROMPT` (around line 42).
- Plan E should NOT need to add new trigger filtering (e.g., "skip Layer B for low-risk shell args") because Layer B already only fires on Layer A ESCALATE.
- The behavior #129 actually wants (Layer B should default toward PASS for benign recon like `ipconfig` / `tasklist /v`) is purely a prompt-engineering problem. The existing prompt at line 42-64 of `layer_b_engine.py` invites the LLM to surface ANY detection risk; #129 wants the LLM to weigh detection cost against actual operator intent. Pure prompt change.
- Plan E may OPTIONALLY also implement the Plan G fix above as a tiny addendum if scope allows, since both touch operator-visible UX around Layer B. But scope-wise they are independent.

### Section: If you want to verify against live data (~10-15 lines)

Subsection header `## Appendix: SQL probe for the T14 timeframe (optional)`.

Provide a copy-pasteable SQL probe for the operator to run optionally. The probe surfaces Layer A + Layer B rows for the smoke-engagement around 2026-05-22:

```powershell
# Per-engagement episodic.db — replace <slug> with the active engagement
# slug from T14 (likely tc4-smoke per #138 smoke notes).
python -c "import sqlite3,sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect(r'C:\Users\jcspi\.redrobin\engagements\<slug>\episodic.db'); rows=c.execute(\"SELECT id,command,verdict,substr(reason,1,80),created_at FROM episodes WHERE command LIKE 'shell %' AND created_at LIKE '2026-05-22%' ORDER BY id\").fetchall(); [print(r) for r in rows]"
```

Expected output IF Hypothesis C is correct (assuming the smoke session's episodic.db is preserved):
- Layer A row for each of `shell ipconfig` / `shell tasklist /v` with `verdict='ESCALATE'`, `reason='novel pattern'` — proves the daemon DID return ESCALATE.
- Layer B retroactive row for each with `verdict='WARN'`, reason about cmd.exe child process.

Also useful as a confirmation: paste the daemon's stdout for the T14 window into a probe for the lines `POST /v1/verdict/quick HTTP/1.1" 200 OK` and `POST /v1/events`. Two 200 OK rows for each `shell` command (one verdict, one input event) confirms the daemon completed its work; the silence on the .cna side then implicates the client-side timeout.

If the operator does not have access to the T14 episodic.db (it was rotated or the engagement was destroyed), the investigation's conclusion stands on the code reading alone (Hypothesis B is structurally false; Hypothesis A is structurally impossible; Hypothesis C is the only consistent explanation given the architecture).

### Section: Closing note (~3 lines)

One paragraph: "Backlog entry #130 was filed with two incorrect file:line citations (preamble location, preamble conditional shape). Future operators reading the backlog after this investigation should treat #130's hypothesis framing as historical context; the actual story is captured here. Status flipped to RESOLVED in `docs/superpowers/backlog.md` Plan D close-out (Task 2)."

- [ ] **Step 2: Verify every file:line citation in the report**

Re-read each cited line in the source files to confirm the line numbers in the report still match HEAD `331a3a7`. The implementer should do a final pass with `grep -n` or Read tool on:
- `adapters/cobaltstrike/redrobin.cna` (lines 181-232, 475-486)
- `adapters/cobaltstrike/aliases/tier1a_gate.sleep` (confirm zero "novel pattern" matches)
- `adapters/cobaltstrike/aliases/tier1b_wrapper.sleep` (lines 7-15 for the `shell` alias body)
- `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` (lines 30, 37)
- `redrobin/application/verdict_pipeline.py` (lines 134, 139-147, 151-191, 193-223)
- `redrobin/application/layer_a_engine.py` (lines 48-67)
- `redrobin/application/layer_b_engine.py` (line 42 ± 5 for SYSTEM_PROMPT)
- `redrobin/application/verdict_pipeline_factory.py` (lines 47-59 for DEFAULT_PASSTHROUGH, 168 for instantiation, 169 for scope)
- `redrobin/application/working_memory_reducer.py` (lines 107-117)
- `redrobin/api/events_router.py` (lines 35-45, 53-93, 96-120)
- `redrobin/api/verdicts_router.py` (lines 126-169)
- `redrobin/api/adapter_events_ws.py` (lines 35-42)
- `redrobin/infrastructure/rules_loader.py` (lines 258-259)
- `rules/shared/domain_admin_enumeration.yaml`, `nbtstat_arp_sweep.yaml`, `winrm_enable.yaml`, `vssadmin_list_shadows.yaml`

If any line number shifted, update the report.

- [ ] **Step 3: Commit (atomic — single commit for the report)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/investigations/2026-05-24-layer-b-triggers.md
git commit -m "$(cat <<'EOF'
docs(investigations): Layer B trigger investigation report (#130)

Audit of Layer B's trigger paths and the "novel pattern" preamble
conditional, written in response to the T14 smoke 2026-05-22 observation
that `shell ipconfig` and `shell tasklist /v` returned Layer B WARN
~10s after dispatch without first surfacing the preamble line.

Verdict on the two #130 hypotheses:
  A (preamble's reason-text filter is too narrow): FALSE. The preamble
    conditional at redrobin.cna:228 is action-equality only; reason text
    is never read for the print decision. The gate's hash hard-codes
    reason="Layer B reviewing" for the ESCALATE branch (line 209).
  B (Layer B has a trigger path beyond ESCALATE): FALSE. Grep across
    redrobin/ finds exactly one caller of _run_layer_b at
    verdict_pipeline.py:139, gated on verdict.action == ESCALATE (line
    134). /v1/events never calls Layer B; the reducer never calls Layer
    B; the WS handlers are subscribe-only.

Most likely actual root cause (Hypothesis C, named in the report): the
gate POST hit the JAR's 200ms client timeout (RedRobinHttp.java:30,37)
while the daemon completed Layer A → ESCALATE and scheduled
_run_layer_b before the response returned. Client falls through to the
fail-open PASS branch (silent), but Layer B fires daemon-side and the
retroactive WARN arrives ~10s later via the WS event stream and
on heartbeat_5s drain (redrobin.cna:475-486).

Two factual errors in backlog #130 surfaced and corrected:
  - Preamble location: redrobin.cna:229, NOT tier1a_gate.sleep
  - Preamble conditional: action-equality only, NOT reason-text-gated

Recommended fix scoped for a future Plan G: ~5 LOC in redrobin.cna to
surface gate degradation to the beacon console (so operators know why
Layer B WARNs sometimes appear without preambles). Sketch in report.
Not implemented here.

Implications for Plan E (#129 prompt tuning): NO scope change needed.
Plan E remains a pure prompt-engineering pass against
layer_b_engine.py:SYSTEM_PROMPT. No new trigger filtering required —
Layer B already only fires on Layer A ESCALATE.

Report: docs/investigations/2026-05-24-layer-b-triggers.md
EOF
)"
```

**Do NOT use `--amend` or `--no-verify`.** If a pre-commit hook fails, fix the issue and create a NEW commit.

---

## Task 2: Backlog close-out for #130

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Capture Task 1's commit SHA**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git log -1 --format=%H docs/investigations/2026-05-24-layer-b-triggers.md
```

Call this `<T1 SHA>`. It is the most recent commit touching the report file.

- [ ] **Step 2: Update the scope table row for Plan D**

In `docs/superpowers/backlog.md` find the row:

```
| D | 3 — Layer B trigger investigation | `#130` | read-only audit, ~30 min | Pending |
```

Replace `Pending` with `**DONE 2026-05-24** (commit <T1 SHA>)`.

- [ ] **Step 3: Update the #130 entry heading and body**

Find the existing entry:

```markdown
### #130 — Layer B fires even when Layer A is silent
**Source:** T14 smoke 2026-05-22
**Symptom:** `shell ipconfig` and `shell tasklist /v` did NOT show "[Red Robin] novel pattern — Layer B reviewing async" but DID get Layer B WARN ~10s later. Either Layer A returned ESCALATE with a different `reason` (tier1a_gate.sleep prints "novel pattern" preamble conditionally on reason text), OR Layer B has a trigger path beyond ESCALATE.
**Files:** `redrobin/application/verdict_pipeline.py:134` (Layer B trigger condition), `adapters/cobaltstrike/aliases/tier1a_gate.sleep` (novel-pattern preamble logic).
**Fix:** Trace `_run_layer_b` invocations vs the Layer A reason text the alias is checking. Either fix the alias preamble to match all ESCALATE paths, OR document that Layer B can fire on other verdict types.
```

Replace with:

```markdown
### #130 — Layer B fires even when Layer A is silent (RESOLVED 2026-05-24 commit <T1 SHA>)
Plan D shipped the investigation report at `docs/investigations/2026-05-24-layer-b-triggers.md`. Both #130 hypotheses ruled out: (A) the preamble's conditional at `redrobin.cna:228` is action-equality only, never reason-text-gated; (B) `_run_layer_b` has exactly one caller (`verdict_pipeline.py:139`), gated on `verdict.action == ESCALATE` — Layer B does NOT fire on `/v1/events` ingestion, the reducer, or any other path. Most likely actual root cause: client-side 200ms HTTP timeout in `RedRobinHttp.java:30,37` while the daemon completed Layer A → ESCALATE and scheduled Layer B before the response returned. The retroactive WARN reached the beacon console ~10s later via the WS event stream (`redrobin.cna:475-486`), independent of the gate POST. Backlog entry mis-cited two facts (corrected in the report): preamble lives in `redrobin.cna:229` (not `tier1a_gate.sleep`), and the preamble conditional is action-equality only (not reason-text-gated). Recommended fix scoped for a future Plan G: ~5 LOC in `redrobin.cna` to surface gate degradation to the beacon console; not implemented here. Plan E (#129) scope confirmed unchanged — pure prompt tuning at `layer_b_engine.py:SYSTEM_PROMPT`.
```

- [ ] **Step 4: Commit the backlog close-out**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/backlog.md
git commit -m "$(cat <<'EOF'
docs(backlog): mark #130 RESOLVED -- Plan D close-out

Plan D Task 1 shipped the investigation report at
docs/investigations/2026-05-24-layer-b-triggers.md (commit <T1 SHA>).
Both #130 hypotheses ruled out by code reading at HEAD 331a3a7;
client-side HTTP timeout identified as the most likely actual root
cause. Plan E (#129) scope confirmed unchanged. Recommended fix for
gate-degradation visibility scoped for a future Plan G.

Scope table row D flipped to DONE.
EOF
)"
```

Replace `<T1 SHA>` with the actual SHA captured in Step 1 before running. Use HEREDOC so the message body retains its newlines verbatim.

- [ ] **Step 5: Verify both commits exist on `dev-juan`**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git log --oneline -3
```

Expected output (top to bottom): T2 (backlog close-out) → T1 (investigation report) → `331a3a7 docs(backlog): mark #116, #120, #111 RESOLVED -- Plan C close-out`.

---

## Self-Review

**Spec coverage:** Plan D produces exactly the artifacts named in the source prompt:
- Investigation report at `docs/investigations/2026-05-24-layer-b-triggers.md` with all seven required sections (Summary, Trigger paths, Preamble, Reproduce, Hypothesis & evidence, Recommended fix, Implications for Plan E). ✓
- Backlog close-out for #130 with citation of Task 1's commit SHA + scope-table row flipped to DONE. ✓
- No code changes — investigation only. ✓
- Atomic commits: two total (T1 = report, T2 = backlog). ✓
- No Claude co-author. No `--amend`. No `--no-verify`. ✓

**Hypotheses settled in the report (with citation):**
- A (preamble's reason-text filter): FALSE — `redrobin.cna:228` is action-equality only. ✓
- B (Layer B has trigger paths beyond ESCALATE): FALSE — grep across `redrobin/` finds exactly one `_run_layer_b` caller at `verdict_pipeline.py:139`. ✓
- C (client-side HTTP timeout — most likely actual cause): documented with the JAR's 200ms cap at `RedRobinHttp.java:30,37` + the gate's PASS fail-open at `redrobin.cna:210-212`. ✓

**Plan E scope decision:** Documented explicitly — no scope change needed. Plan E remains a pure prompt-engineering pass against `layer_b_engine.py:SYSTEM_PROMPT`. ✓

**Recommended Plan G (fix sketch):** Named in the report with file:line + ~5 LOC, deferred — NOT implemented. ✓

**Citation rigor:** Every file:line in the report is read directly from HEAD `331a3a7` (Step 2 of Task 1 enforces a verification pass before commit). ✓

Ready for execution.
