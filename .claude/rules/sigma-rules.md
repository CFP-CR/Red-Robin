---
paths:
  - "rules/**"
---

# Red Robin — Layer A OPSEC Rules (rules/)

`rules/` holds the Sigma/OPSEC **Layer-A rules** (product data) — NOT Claude instructions (that's
`.claude/rules/`). YAML in `rules/shared/<rule_id>.yaml` (or `rules/personal/` for operator-private).
Loaded by `infrastructure/rules_loader.py`, consumed by `LayerAEngine`, schema in
`redrobin/domain/rules.py`. Hot-reloaded (watchdog on the rules dir).

## Two match shapes (see domain/rules.py)

1. **Native**: `match.command` + optional `match.arg_pattern`.
2. **Sigma-style**: `match_predicates` (tuple of `MatchPredicate`, AND-wise) supporting
   `CommandLine|contains`, `Image|endswith` (`.exe` basename coercion), plus `equals`/`startswith`/
   `endswith`, case-insensitive, list-OR. Unsupported fields/modifiers become a `_NeverMatchPredicate`
   (loads but never fires — warn, don't silently mis-match).

## Rule fields

`id`, `match.command`, `match.arg_pattern?`, `severity` (INFO/WARN/DANGER), `reason`, `suggestions?`,
`sources`.

## Adding a rule

Use the `new-sigma-rule` skill (or CONTRIBUTING "Adding a rule"). Add a test in
`tests/integration/test_bootstrap_rules.py` for core rules.
