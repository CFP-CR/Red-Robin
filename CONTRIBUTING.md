# Contributing to Red Robin

Thank you for your interest in contributing. Red Robin is a small, opinionated
project. Here's how to work with us.

## Architecture rules (enforced in CI)

- **Layered architecture**: `domain/` → `application/` → `infrastructure/` /
  `api/` / `tui/`. CI's `import-linter` rejects violations.
- **No `from infrastructure import *` in `api/` or `tui/`** — use an
  application-layer facade (see `redrobin/application/transport_auth.py` for
  the pattern).
- **Adapter is the sole C2 boundary**: Red Robin core speaks the wire protocol
  only. C2-specific knowledge (REST API, Aggressor primitives, gRPC stubs)
  lives entirely inside `adapters/<name>/`.

## Development setup

```bash
git clone https://github.com/CFP-CR/Red-Robin.git
cd Red-Robin
uv sync --all-extras
uv run pre-commit install
```

## Style + tooling

- **`uv`** for builds + dep resolution (no `pip install` directly)
- **`ruff`** for lint + format (`uv run ruff check` + `uv run ruff format`)
- **`mypy`** strict on `redrobin/domain` + `redrobin/application` (gradual elsewhere)
- **`import-linter`** for layered enforcement (`uv run lint-imports --config importlinter.cfg`)
- **`pytest`** + `pytest-asyncio` for tests
- **TDD**: write the failing test, verify red, implement, verify green, commit.

## Commit conventions

- **Atomic commits**: one logical change per commit (no `git add -A` mass commits)
- **Conventional commit messages**: `feat(scope): description`, `fix(scope): ...`,
  `test(scope): ...`, `docs: ...`, `style: ...`, `chore: ...`
- **No AI co-author trailers**: do not add `Co-Authored-By: Claude` or similar.

## Pull request flow

1. Create a topic branch from `dev-juan` (`feat-X`, `fix-Y`, `docs-Z`).
2. Open a PR back into `dev-juan` (the integration branch). `main` is updated from `dev-juan` via PR at release.
3. CI must pass: ruff + mypy + import-linter + pytest.
4. Code review by another operator before merge.
5. Squash-merge for small PRs; preserve atomic commits for larger ones.

## Branch protection

- `main` is protected. PRs only.
- `dev-jose` / `dev-juan` are operator working branches (do not push directly).

## Tests

- All new code requires tests.
- Tests live in `tests/unit/` (pure unit) or `tests/integration/` (HTTP/integration).
- Adapter tests live in `adapters/<name>/tests/`.

## Adding a rule

1. Create YAML in `rules/shared/<rule_id>.yaml` (or `rules/personal/` for
   operator-private rules).
2. Schema: `id`, `match.command`, `match.arg_pattern` (optional), `severity`
   (INFO/WARN/DANGER), `reason`, optional `suggestions`, `sources`.
3. Add a test in `tests/integration/test_bootstrap_rules.py` if it's a core rule.

## Adding an adapter

1. Create `adapters/<name>/{__init__.py, adapter.py, manifest.toml}`.
2. Manifest schema defined in spec §4 Componente 8.
3. Adapter speaks only the wire protocol (see `protocol/schemas/v0.1/`).
4. Adapter tests in `adapters/<name>/tests/`.

## Code of conduct

Be respectful. We're building offensive security tooling — that comes with
ethical responsibility. Don't ship features that primarily enable
non-authorized targets.

## Claude Code

This repo ships committed Claude Code config under `.claude/` (path-scoped `rules/`, `skills/`) plus a
root `CLAUDE.md`. Run `/memory` inside a session to see which instructions and rules are loaded.
