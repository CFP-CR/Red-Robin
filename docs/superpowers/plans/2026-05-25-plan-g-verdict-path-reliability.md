# Plan G: Verdict-Path Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OPSEC console gate reliably block dangerous commands — it must never silently fail open — and stop Layer B retroactive verdicts from corrupting working memory with `command='?'`.

**Architecture:** Two independent fixes.
1. **#141 (Python daemon):** thread the real `command` through the Layer B retroactive `opsec.verdict` payload so the reducer stops defaulting it to `?`.
2. **#144 (`.cna` only — NO JAR rebuild):** `redrobin_query_quick` stops conflating an empty/failed POST with PASS (returns a distinct `UNAVAILABLE`); `redrobin_http_post` retries ONCE on a connection-class error (fixes the stale idle-closed keep-alive trigger); `redrobin_gate` gains a `$dangerous` flag and, on `UNAVAILABLE`, fail-CLOSES for dangerous (tier1a) verbs while loud-warning-but-proceeding for the rest.

**Why no JAR/daemon change:** measurement 2026-05-24 (80 probes / 120s) showed the daemon answers `/v1/verdict/quick` in 22–77ms (p95=70, 0% over 200ms) even under summarizer+promoter load. The fail-open is NOT daemon latency — it is purely client-side: (a) any POST failure was treated as PASS, and (b) a stale/idle-closed keep-alive connection (uvicorn drops idle conns ~5s; operators type sporadically) failed the first POST after a gap. Retry-once + UNAVAILABLE-fail-closed fixes both without raising the 200ms cap or rebuilding the JAR. (Raising the cap / daemon-synced danger list are deferred hardenings: #154.)

**Tech Stack:** Python 3.11 + pytest-asyncio (daemon); Cobalt Strike Aggressor/Sleep (`.cna`). No Java/JAR/build changes.

**Verification model:** Task 1 (#141) is unit-tested. Tasks 2–3 (`.cna`) have NO offline Sleep linter — they are validated ONLY by the operator-smoke in Task 4 against a LIVE beacon. This is mandatory and non-negotiable: this session proved 393 green unit tests coexisted with the gate failing open.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/application/verdict_pipeline.py` | verdict orchestration | #141: add `command` to `_on_retroactive` signature + payload; pass it from `_run_layer_b` |
| `tests/integration/test_verdict_pipeline_layer_b_command.py` | #141 regression | CREATE — assert retro payload carries `command` |
| `adapters/cobaltstrike/redrobin.cna` | gate + HTTP helpers | #144: `redrobin_http_post` retry-once; `redrobin_query_quick` UNAVAILABLE; `redrobin_gate` `$dangerous` fail-closed |
| `adapters/cobaltstrike/aliases/tier1a_gate.sleep` | dangerous direct-command aliases | #144: pass `true` (dangerous) as 4th gate arg on all 16 aliases |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | operator-smoke spec | #144: add the Plan G acceptance smoke section |

`tier1b_wrapper.sleep` and `tier2_warn.sleep` are intentionally NOT changed: their aliases call `redrobin_gate` with 3 args, so `$dangerous` is `$null` (falsey) → loud-warn-but-proceed, which is the desired behavior for arg-dependent vehicles.

---

### Task 1: #141 — thread `command` into the Layer B retroactive verdict

**Files:**
- Modify: `redrobin/application/verdict_pipeline.py:193-223` (`_on_retroactive`) and `:185-191` (the call in `_run_layer_b`)
- Test: `tests/integration/test_verdict_pipeline_layer_b_command.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_verdict_pipeline_layer_b_command.py`:

```python
"""Layer B retroactive verdict must carry the real command, not '?'.

#141: `_run_layer_b` had `command` but never passed it to `_on_retroactive`, so the
opsec.verdict payload omitted it. The reducer then defaulted to '?'
(working_memory_reducer.py:166), corrupting episodes, findings, and promoted_lessons
(longterm.db had garbage `?/BLOCK count=13`). Mirror of test_verdict_pipeline_layer_b_beacon_id.py.
"""
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline_factory import build_pipeline_for_test


@pytest.mark.asyncio
async def test_layer_b_retroactive_includes_command() -> None:
    bus = EventBus()
    queue = await bus.subscribe()
    pipeline = build_pipeline_for_test(event_bus=bus)

    await pipeline._on_retroactive(
        correlation_id="evt_abc123",
        beacon_id="bid_TESTBEACON",
        command="mimikatz logonpasswords",
        original_verdict="ESCALATE",
        new_verdict="BLOCK",
        reason="lsass credential extraction",
    )

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["type"] == "opsec.verdict"
    payload = event["payload"]
    assert payload["command"] == "mimikatz logonpasswords"
    assert payload["beacon_id"] == "bid_TESTBEACON"
    assert payload["verdict"] == "BLOCK"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_verdict_pipeline_layer_b_command.py -v`
Expected: FAIL with `TypeError: _on_retroactive() got an unexpected keyword argument 'command'`

- [ ] **Step 3: Add `command` to `_on_retroactive` (signature + payload)**

In `redrobin/application/verdict_pipeline.py`, change the `_on_retroactive` signature (currently ends `new_verdict: str,` then `reason: str,`). Add `command: str = ""` after `beacon_id: str,`:

```python
    async def _on_retroactive(
        self,
        *,
        correlation_id: str,
        beacon_id: str,
        command: str = "",
        original_verdict: str,
        new_verdict: str,
        reason: str,
    ) -> None:
```

And add `"command": command,` to the published payload:

```python
        await self._bus.publish(
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": correlation_id,
                    "beacon_id": beacon_id,
                    "command": command,
                    "verdict": new_verdict,
                    "previous_verdict": original_verdict,
                    "reason": reason,
                    "source": "layer_b",
                },
            }
        )
```

(Default `= ""` keeps the existing `test_verdict_pipeline_layer_b_beacon_id.py` and `test_retroactive_journey.py` callers passing unchanged; production always passes the real command via Step 4.)

- [ ] **Step 4: Pass `command` from `_run_layer_b`**

In the same file, the `_run_layer_b` call to `_on_retroactive` (currently passes `correlation_id`, `beacon_id`, `original_verdict`, `new_verdict`, `reason`). Add `command=command,`:

```python
        await self._on_retroactive(
            correlation_id=source_event_id,
            beacon_id=beacon_id,
            command=command,
            original_verdict=original_action.value,
            new_verdict=retro.action.value,
            reason=retro.reason,
        )
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `uv run pytest tests/integration/test_verdict_pipeline_layer_b_command.py -v`
Expected: PASS

- [ ] **Step 6: Run the full verdict suite to confirm no regressions**

Run: `uv run pytest tests/unit/test_verdict_pipeline.py tests/integration/test_verdict_emission.py tests/integration/test_verdict_pipeline_layer_b_beacon_id.py tests/e2e/test_retroactive_journey.py -v`
Expected: all PASS (the `= ""` default preserves the existing callers)

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin add redrobin/application/verdict_pipeline.py tests/integration/test_verdict_pipeline_layer_b_command.py
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin commit -m "fix(verdict): thread command through Layer B retroactive verdict (#141)"
```

---

### Task 2: #144 (A+D) — `.cna` HTTP retry-once + distinct UNAVAILABLE status

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna:77-99` (`redrobin_http_post`) and `:181-213` (`redrobin_query_quick`)

No unit test — `.cna` has no offline Sleep linter. Validated in Task 4.

- [ ] **Step 1: Add retry-once on connection-class errors to `redrobin_http_post`**

Replace the body of `sub redrobin_http_post` (lines 77-99) with:

```sleep
sub redrobin_http_post {
    global('$REDROBIN_HTTP $REDROBIN_URL $REDROBIN_TOKEN');
    local('$path $body $retry $err');
    ($path, $body, $retry) = @_;

    try {
        return [$REDROBIN_HTTP post: $path, $body];
    } catch $e {
        $err = redrobin_sanitize_error($e);
        # Stale / idle-closed keep-alive (#144): the first POST after an idle gap
        # hits a dead pooled connection. Retry ONCE so attempt 2 gets a fresh one.
        # GATED on $retry: this helper is SHARED with the NON-idempotent /v1/events
        # posts (redrobin.cna:274/289) and the handshake (own retry) — only the
        # read-only verdict query opts in. Blindly retrying events would duplicate
        # episodes/findings/credentials. (Audit BLOCKER fix.)
        if ($retry && ($err hasmatch 'ConnectException' || $err hasmatch 'reset' || $err hasmatch 'closed' || $err hasmatch 'GOAWAY' || $err hasmatch 'EOF')) {
            try {
                return [$REDROBIN_HTTP post: $path, $body];
            } catch $e2 {
                $err = redrobin_sanitize_error($e2);
            }
        }
        if ($err hasmatch 'ConnectException' || $err hasmatch 'refused') {
            warn("[Red Robin] daemon connection refused at " . $REDROBIN_URL . " — is the daemon running?");
        } else if ($err hasmatch 'HttpTimeout' || $err hasmatch 'timed out') {
            warn("[Red Robin] daemon timeout at " . $REDROBIN_URL . " (>200ms) — slow network or overloaded daemon");
        } else if ($err hasmatch 'UnknownHost') {
            warn("[Red Robin] cannot resolve daemon host in " . $REDROBIN_URL);
        } else if ($err hasmatch 'HttpException') {
            warn("[Red Robin] daemon returned non-2xx for " . $path . " — see daemon log");
        } else {
            warn("[Red Robin] HTTP error: " . $err);
        }
        return "";
    }
}
```

- [ ] **Step 2: Make `redrobin_query_quick` return UNAVAILABLE on an empty response**

In `sub redrobin_query_quick`, FIRST change the POST line to opt into the retry (3rd arg `true` — only the verdict query retries; the shared helper's other callers stay un-retried):

```sleep
    $response = redrobin_http_post("/v1/verdict/quick", $body, true);
```

THEN immediately AFTER that line and BEFORE the `if ($response hasmatch '"action":"BLOCK"')` block, insert:

```sleep
    # Empty = the POST failed (timeout / connection / non-2xx) even after the
    # retry in redrobin_http_post. This is NOT a PASS — the daemon never rendered
    # a verdict. Return a distinct UNAVAILABLE so the gate can fail-closed for
    # dangerous verbs instead of silently letting the command through (#144).
    if ($response eq "") {
        return %(action => "UNAVAILABLE", reason => "daemon unreachable — command NOT vetted");
    }
```

- [ ] **Step 3: Commit**

```bash
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin add adapters/cobaltstrike/redrobin.cna
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin commit -m "fix(cna): retry-once on stale keep-alive + distinct UNAVAILABLE status (#144)"
```

---

### Task 3: #144 (B+C) — danger-aware fail-closed gate

**Files:**
- Modify: `adapters/cobaltstrike/redrobin.cna:217-231` (`redrobin_gate`)
- Modify: `adapters/cobaltstrike/aliases/tier1a_gate.sleep` (16 `redrobin_gate(...)` call sites)

- [ ] **Step 1: Add the `$dangerous` parameter + UNAVAILABLE handling to `redrobin_gate`**

Replace `sub redrobin_gate` (lines 217-231) with:

```sleep
sub redrobin_gate {
    local('$bid $cmd $raw $dangerous $verdict');
    ($bid, $cmd, $raw, $dangerous) = @_;
    $verdict = redrobin_query_quick($bid, $cmd, $raw);

    # Daemon could not be reached / no verdict rendered (#144). Never silently
    # fail open: fail-CLOSED for dangerous verbs (tier1a passes $dangerous=true);
    # for arg-dependent vehicles (tier1b/tier2, no flag) loud-warn but proceed so
    # a daemon hiccup doesn't block benign recon.
    if ($verdict['action'] eq "UNAVAILABLE") {
        if ($dangerous) {
            berror($bid, "[Red Robin] gate UNAVAILABLE (daemon unreachable) — BLOCKING " . $cmd . " (dangerous, cannot verify). Retry once the daemon is reachable.");
            return false;
        }
        berror($bid, "[Red Robin] gate UNAVAILABLE (daemon unreachable) — " . $cmd . " NOT vetted, proceeding. Check the daemon.");
        return true;
    }

    if ($verdict['action'] eq "BLOCK") {
        berror($bid, "[Red Robin] DANGER: " . $verdict['reason']);
        return false;
    }
    if ($verdict['action'] eq "WARN") {
        blog($bid, "[Red Robin] WARN: " . $verdict['reason']);
    }
    if ($verdict['action'] eq "ESCALATE") {
        blog($bid, "[Red Robin] novel pattern — Layer B reviewing async");
    }
    return true;
}
```

- [ ] **Step 2: Pass `true` (dangerous) on every tier1a gate call**

In `adapters/cobaltstrike/aliases/tier1a_gate.sleep`, add a 4th argument `true` to every `redrobin_gate(...)` call. Example for `mimikatz`:

```sleep
        if (redrobin_gate($1, "mimikatz", %p['raw'], true)) {
```

Apply the identical 4th-arg `, true` to the `redrobin_gate(...)` call in each of these 16 tier1a aliases: **mimikatz, dcsync, hashdump, logonpasswords, kerberos_ticket_use, psexec, wmi, winrm, jump, spawn, bypassuac, getsystem, elevate, inject, dllinject, psinject.** (hashdump/logonpasswords/getsystem use a literal raw e.g. `redrobin_gate($1, "hashdump", "hashdump", true)`; the rest use `%p['raw']`.) Do NOT touch `tier1b_wrapper.sleep` (7 aliases) or `tier2_warn.sleep` (5 aliases: screenshot, keylogger, screenwatch, browserpivot, socks) — their 3-arg calls leave `$dangerous` null → warn-proceed, which is intended (tier2 is "WARN at most, never blocks" by design). Also leave the doc-comment at `tier1a_gate.sleep:31` unchanged — it is a usage example inside `redrobin_alias_parse`'s header, NOT a live `redrobin_gate` call, so it gets no `, true` and the Step-3 grep-count of 16 holds.

- [ ] **Step 3: Sanity-check the edit count**

Run: `grep -c "redrobin_gate(.*true)" adapters/cobaltstrike/aliases/tier1a_gate.sleep`
Expected: `16`
Run: `grep -c "redrobin_gate(" adapters/cobaltstrike/aliases/tier1b_wrapper.sleep`
Expected: `7` (all WITHOUT `true`)

- [ ] **Step 4: Commit**

```bash
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin add adapters/cobaltstrike/redrobin.cna adapters/cobaltstrike/aliases/tier1a_gate.sleep
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin commit -m "fix(cna): danger-aware fail-closed gate on daemon-unavailable (#144)"
```

---

### Task 4: Operator-smoke acceptance (LIVE beacon — mandatory gate)

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md` (add a "Plan G — gate reliability" section documenting these steps)

This is the ONLY behavioral validation for Tasks 2–3 (no offline Sleep linter). The operator runs it; the AI inspects daemon-side artifacts. Acceptance = ALL checks pass.

- [ ] **Step 1: Setup** — daemon running; `redrobin.cna` freshly unloaded+reloaded in Script Manager; one active beacon; engagement with non-empty scope.

- [ ] **Step 2: Warm-path block (regression baseline)** — type `mimikatz logonpasswords`, then immediately a second time.
Expected: BOTH show `[-] [Red Robin] DANGER: ...`; NEITHER shows "Tasked beacon to run mimikatz"; no credential output.

- [ ] **Step 3: Idle-gap block (the #144 fix)** — wait 30 seconds (let the keep-alive connection idle-close), then type `mimikatz logonpasswords`.
Expected: `[-] [Red Robin] DANGER: ...`, NO execution. (Before the fix this silently executed.) The retry-once re-establishes the connection transparently.

- [ ] **Step 4: Benign passes** — type `shell whoami`.
Expected: `[Red Robin] novel pattern — Layer B reviewing async` then executes (`domain\user` output).

- [ ] **Step 5: Daemon-down fail-CLOSED for dangerous** — STOP the daemon (`Ctrl+C`). Type `mimikatz logonpasswords`.
Expected: `[-] [Red Robin] gate UNAVAILABLE (daemon unreachable) — BLOCKING mimikatz ...`; NO execution.

- [ ] **Step 6: Daemon-down warn-proceed for benign** — with the daemon still down, type `shell whoami`.
Expected: `[Red Robin] gate UNAVAILABLE (daemon unreachable) — shell NOT vetted, proceeding ...` THEN the command executes.

- [ ] **Step 7: #141 end-to-end** — restart the daemon; type a HARMLESS command that ESCALATEs to a Layer B WARN (e.g. `shell findstr /si password *.txt` — credential-discovery → Layer B WARN, but safe to run). Do NOT use a real LSASS-dump command: on ESCALATE the gate PROCEEDS before Layer B returns, so a dangerous command would actually execute. After ~10s, AI inspects the engagement `episodic.db`.
Expected: the Layer B retroactive row's `command` is the real verb (`shell`), NOT `?`.

- [ ] **Step 8: Commit the smoke spec**

```bash
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin add adapters/cobaltstrike/tests/test_cna_smoke.md
git -C C:/Users/jcspi/Documents/dev-projects/Red-Robin commit -m "test(cna): add Plan G gate-reliability operator-smoke (#144)"
```

---

## Self-Review

**Spec coverage:**
- #144 silent fail-open → Tasks 2 (UNAVAILABLE not PASS) + 3 (fail-closed). ✓
- #144 stale-connection trigger → Task 2 retry-once. ✓
- #144 posture A (fail-closed dangerous + warn-proceed rest) → Task 3 `$dangerous`. ✓
- #144 danger classification (i) tier1a-derived → Task 3 Step 2. ✓
- #141 command='?' → Task 1. ✓
- Acceptance (mimikatz quick + idle-gap block; #141 real command) → Task 4. ✓

**Placeholder scan:** no TBD/TODO; all code shown; the 16-alias change is fully enumerated by name + rule. The Step-2 tier1a edit is mechanical-but-explicit (acceptable: exact transformation + exact alias list).

**Type/name consistency:** `UNAVAILABLE` is produced in `redrobin_query_quick` (Task 2 Step 2) and consumed in `redrobin_gate` (Task 3 Step 1) — matching string. `$dangerous` 4th arg set in tier1a (Task 3 Step 2), read in `redrobin_gate` (Task 3 Step 1). `command` kwarg added in `_on_retroactive` (Task 1 Step 3) and passed by `_run_layer_b` (Task 1 Step 4) — matching name.

**Deferred (NOT in this plan):** raising the JAR 200ms cap + daemon-synced danger list (#154); both are P3 hardenings — the retry-once + fail-closed make them unnecessary for correctness.
