---
name: auditing-plans
description: Use when a Red Robin design spec and/or implementation plan is written and needs verification against the actual codebase before implementation begins — after writing-plans, before executing. Triggers on plan audit, spec audit, pre-implementation review, "assume nothing".
---

# Auditing Plans (Red Robin)

Cross-reference every claim in the **spec AND the plan** against the actual Red Robin codebase before any code is written. The author assumes things about the code; the auditor verifies them. **Assume nothing.**

**Announce at start:** "Using the auditing-plans skill to audit the spec and plan."

## When to use

- A spec (`docs/superpowers/specs/`) and/or plan (`docs/superpowers/plans/`) is complete, before implementation.
- After `writing-plans`, before `subagent-driven-development` / `executing-plans`.
- Any time a doc asserts a fact about the wire protocol, layering, rules, the `.cna`, or the CI gates.

## Method

1. **Read both docs fully** — spec first, then plan. Audit them **together**: they can contradict each other, and the plan can drift from the spec.
2. **Don't trust citations.** Every file path, line number, type, route, field name, and enum in the docs is a *claim* — `Read` the actual source and confirm it. For a large plan, dispatch parallel Explore agents for breadth.
3. **Walk each task** against the files it touches — read them, don't assume.
4. **Check claims against the project rules** (they encode the verified facts; a doc claim that contradicts a rule is a finding until proven against source):
   - `.claude/rules/wire-protocol.md` — event types + fields (`bid`, never `beacon_id`; `OpsecVerdictEvent` = `source_event_id/severity/reason/rule_id?/suggestions[]`; `Severity` INFO/WARN/DANGER/ESCALATE vs `VerdictAction` PASS/WARN/BLOCK/ESCALATE — **no `ALLOW`**); route `POST /v1/verdict/quick`; port 7777; schemas `additionalProperties:false`.
   - `.claude/rules/architecture.md` — the 4 import-linter contracts by name; layer direction; the facade pattern; `LLMProvider` is the only ABC.
   - `.claude/rules/sigma-rules.md` — OPSEC rules live in the **top-level `rules/`** (NOT `.claude/rules/`); severity is INFO/WARN/DANGER; the match schema.
   - `.claude/rules/aggressor-cna.md` — **`.cna` changes are validated by a live-beacon operator smoke, never by CI**; `hasmatch`→`ismatch`; `$3`/`$5`; Script Manager reload required.
   - `.claude/rules/testing.md` — TDD; adapter tests (`adapters/<name>/tests/`) are NOT in CI's `pytest tests/`; the local gotchas.
5. **Verify the gates + acceptance.** The 5 CI gates are `ruff check` / `ruff format --check` / `mypy redrobin/domain redrobin/application` / `lint-imports --config importlinter.cfg` / `pytest tests/`. A doc claiming a `.cna`/gate/TUI change is "verified by CI" is wrong — that is an operator smoke.
6. **Compile a report** with severity, then **present it** — issues AND what the docs get right.

## Severity

| Level | Criteria |
|---|---|
| **CRITICAL** | Would ship a bug or security hole, or break the daemon/adapter: wrong field name, nonexistent enum value, a rule the loader never reads, a false acceptance criterion. |
| **IMPORTANT** | Runtime error, contract violation, or maintainability damage that must be fixed before implementing. |
| **MINOR** | Stale reference or inconsistency that won't block. |

## Report template

```markdown
# [Feature] — Spec & Plan Audit
## Methodology    [files read, rules checked, agents launched]
## CRITICAL (N)   ### [title] — [consequence]  [doc location + source evidence file:line + fix]
## IMPORTANT (N)  ### [title] — [same format]
## MINOR (N)
## What the docs get RIGHT   [confirmed-correct decisions — ALWAYS include this]
## Summary   | Severity | Count | Fix before implementation? |
```

## Common auditor mistakes

| Mistake | Prevention |
|---|---|
| Trust the doc's citations | Always `Read` the actual source file. |
| Audit the plan but not the spec | Audit **both** — the plan drifts from the spec, and they can contradict. |
| Only report what's wrong | Include the "gets RIGHT" section — accurate praise builds trust. |
| Report an issue without a fix | Every finding names the correct value / concrete fix. |
| Confuse `rules/` with `.claude/rules/` | Top-level `rules/` = Sigma product data; `.claude/rules/` = Claude tooling. |
