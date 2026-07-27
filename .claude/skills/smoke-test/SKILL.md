---
name: smoke-test
description: Run the live-beacon operator acceptance smoke for a .cna / capture / TUI change in Red Robin
argument-hint: "[engagement-slug]"
---

# Live-Beacon Smoke Test

The ONLY real acceptance test for `.cna`, capture, and TUI changes (unit tests and CI cannot exercise
the CS client). Requires a live Cobalt Strike beacon and the operator.

Red Robin is **advisory-only**: the `.cna` observes and never intercepts. So the smoke proves
*capture → enricher → assistant*, not interception. A dangerous command must dispatch **immediately**,
with no `[Red Robin]` console message — that is a PASS, not a failure.

## Procedure

1. Start the daemon on the working branch: `uv run redrobin daemon start` — use a clean engagement
   (e.g. `manual-smoke`) to avoid polluting `e2e-verify`. Set API keys (or `REDROBIN_ALLOW_DEGRADED=1`
   to smoke the non-LLM path).
2. In CS Client → Script Manager, **load/reload** `adapters/cobaltstrike/redrobin.cna` (a `.cna` edit
   does NOT take effect without a reload).
3. Confirm the load + handshake lines in the console:
   `[Red Robin] .cna loaded; capture active (observe-only, no interception).` then
   `[Red Robin] handshake OK; session=ses_...`.
4. Run the acceptance cases for the change under test — typically: a command dispatches with no
   interception, its output reaches working memory, and the assistant advises in the TUI chat.
5. Watch for the failure modes recorded in the runbooks.

## References (read, don't duplicate)

- `docs/smoke-test.md` — the full smoke procedure.
- `docs/superpowers/runbooks/` — per-feature acceptance runbooks.
- `.claude/rules/aggressor-cna.md` — the `.cna` gotchas.
