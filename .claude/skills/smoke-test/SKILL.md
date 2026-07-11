---
name: smoke-test
description: Run the live-beacon operator acceptance smoke for a gate / .cna / TUI change in Red Robin
argument-hint: "[engagement-slug]"
---

# Live-Beacon Smoke Test

The ONLY real acceptance test for `.cna`, gate, and TUI changes (unit tests and CI cannot exercise the
CS client). Requires a live Cobalt Strike beacon and the operator.

## Procedure

1. Start the daemon on the working branch: `uv run redrobin daemon start` — use a clean engagement
   (e.g. `manual-smoke`) to avoid polluting `e2e-verify`. Set API keys (or `REDROBIN_ALLOW_DEGRADED=1`
   for gate-only tests).
2. In CS Client → Script Manager, **load/reload** `adapters/cobaltstrike/redrobin.cna` (a `.cna` edit
   does NOT take effect without a reload).
3. Confirm the handshake line in the console: `[Red Robin] handshake OK; session=ses_...`.
4. Run the acceptance cases for the change under test (BLOCK a dangerous command, PASS a benign one,
   confirm output observation reaches working memory, etc.).
5. Watch for the failure modes recorded in the runbooks.

## References (read, don't duplicate)

- `docs/smoke-test.md` — the full smoke procedure.
- `docs/superpowers/runbooks/` — per-feature acceptance runbooks.
- `.claude/rules/aggressor-cna.md` — the `.cna` gotchas.
