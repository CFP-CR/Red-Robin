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
