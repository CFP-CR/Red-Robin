# Phase 1 Spike Results — Aggressor alias args + Sleep HTTP latency

This document captures the empirical results from running the Phase 1 spikes
against a live Cobalt Strike 4.12 client. The results lock in the canonical
alias body pattern used by `redrobin.cna` (Task 35) and validate the two-tier
verdict protocol's latency budget (spec §1.2.1).

## Run instructions

1. **Spike 1 — alias args (`adapters/cobaltstrike/spike/test_args.cna`)**:
   - CS Client → Scripts → Load → select the `.cna`
   - In any beacon console, run each test case in the table below and copy
     the four `\$0`..`\$3` output lines into the "Observed" column.

2. **Spike 2 — HTTP latency (`adapters/cobaltstrike/spike/test_http_latency.cna`)**:
   - Start stub daemon: `uv run python adapters/cobaltstrike/spike/stub_daemon.py`
   - CS Client → Scripts → Load → select the `.cna`
   - In any beacon console, run `rrlatency` once.
   - Copy the `[Red Robin Spike]` output block into the "Result" section below.

## Results — Alias arg semantics

| Test case | Expected | Observed |
|---|---|---|
| `rrspike single` | $2 = "single", $3 = "" | _(operator fills in)_ |
| `rrspike one two three` | $2 = "one", $3 = "two", $4 = "three" | _(operator fills in)_ |
| `rrspike "quoted string with spaces"` | $2 = "quoted string with spaces" (grouped) | _(operator fills in)_ |
| `rrspike mixed "quoted arg" plain` | $2 = "mixed", $3 = "quoted arg", $4 = "plain" | _(operator fills in)_ |
| `rrspike --flag value --other val2` | $2 = "--flag", $3 = "value", $4 = "--other", $5 = "val2" | _(operator fills in)_ |

### Verdict on alias body pattern

Based on observed behavior:

- [ ] **CONFIRMED**: `$2` is first whitespace-delimited token (not the entire arg string)
- [ ] **CONFIRMED**: `$3`, `$4`, ... are subsequent tokens
- [ ] **CONFIRMED**: Quoted strings group into one token

If all three confirm, the canonical alias body pattern is:

```sleep
alias <cmd> {
    if (redrobin_gate($1, "<cmd>", $0)) {
        local('$args');
        $args = substr($0, strlen("<cmd> ")+1);  # raw arg string, regardless of token count
        &b<cmd>($1, $args);
    }
}
```

This pattern is used in all 24 Tier 1A/1B alias stubs in `redrobin.cna` (Task 35).

## Results — HTTP latency

```
[Red Robin Spike: HTTP latency]
Samples: _____
Avg:    _____ ms
Max:    _____ ms
Budget: 150ms
Result: _____  (PASS / FAIL)
```

### Verdict on latency budget

- [ ] **PASS**: 150ms hard-timeout in alias body's `redrobin_query_quick()` is viable.
  Two-tier verdict protocol (Layer A sync, Layer B async retroactive) is locked in
  per spec §1.2.1.

- [ ] **FAIL**: Latency exceeds budget. Required action:
  - Option A: bump hard-timeout to 250ms (acceptable per spec §1.2.1) and re-test.
  - Option B: if 250ms also fails, downgrade Path 1 to advisory-only and rely on
    Path 3 post-exec observation as primary verdict channel. Document this in
    spec as v0.1 behavior, plan async-only daemon dispatch for the alias body.

## Status

- [ ] Spike 1 (alias args) — RUN BY OPERATOR, RESULTS FILLED IN
- [ ] Spike 2 (HTTP latency) — RUN BY OPERATOR, RESULTS FILLED IN

Until both are checked, the canonical alias body pattern (Task 35) is provisional.
