---
name: new-sigma-rule
description: Add and validate a Red Robin Layer A OPSEC rule (rules/)
argument-hint: "<rule_id>"
---

# Add a Layer A OPSEC Rule

1. Create `rules/shared/<rule_id>.yaml` (or `rules/personal/` for operator-private):
   ```yaml
   id: <rule_id>
   match:
     command: <command>
     arg_pattern: <optional regex>
   severity: WARN   # INFO | WARN | DANGER
   reason: <why this is risky>
   suggestions:
     - <stealthier alternative>
   sources:
     - <detection reference / URL>
   ```
2. (Sigma-style alternative) use `match_predicates` — see `.claude/rules/sigma-rules.md`.
3. Validate: the rule loads (start the daemon or run the loader) and `LayerAEngine` matches the
   intended command. Unsupported fields degrade to a never-match predicate with a warning — check logs.
4. Add a test in `tests/integration/test_bootstrap_rules.py` for a core rule.
5. Run gates: `uv run pytest tests/integration/test_bootstrap_rules.py -v` + the full 5 CI gates.

## References

- `.claude/rules/sigma-rules.md`, `docs/sigma-support.md`, CONTRIBUTING "Adding a rule".
