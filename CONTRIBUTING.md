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
  lives entirely inside `adapters/<name>/`. The adapter **captures** (observes
  commands + output and publishes events); the core **advises**. An adapter
  never gates or intercepts the operator's command path.

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

1. Create a topic branch from the integration branch `dev` (`feat-X`, `fix-Y`, `docs-Z`).
2. Open a PR back into `dev`. `main` is updated from `dev` via PR at release.
3. CI must pass: ruff + mypy + import-linter + pytest.
4. Code review by another operator before merge.
5. Squash-merge for small PRs; preserve atomic commits for larger ones.

## Branch protection

- `main` is protected (production) — PRs only, no direct pushes.
- `dev` is the shared integration branch — PRs only, no direct pushes.

## Tests

- All new code requires tests.
- `tests/unit/` (pure logic), `tests/integration/` (HTTP), `tests/e2e/` (whole flow).
- Adapter tests live in `adapters/<name>/tests/`.
- Run `pytest` from a directory with no `.env` (e.g. `tools/`) — a `.env` in the
  working directory is picked up by the CLI code under test and can mask failures.
- Full gate list and the live-Beacon acceptance procedures: [TESTING.md](TESTING.md).

## Interactive subcommands

`redrobin init` set the pattern; anything that prompts should follow it.

1. Interactive by default — prompt for missing input via `getpass` (secrets) or
   `input()` (everything else).
2. Every required value is also an explicit flag (`--anthropic-key`,
   `--engagement-slug`). A flag skips its prompt.
3. `--non-interactive` suppresses all prompts. A missing required value exits 2
   with a stderr error that **names the flag to pass**.
4. Refuse to prompt when `sys.stdin.isatty()` is false, regardless. Exit 2
   pointing at `--non-interactive`. This covers CI, `< file`, and pipes, where
   `getpass` would silently fall back to plain-text echo.
5. Idempotent on disk. Re-running never rewrites an existing operator-owned file.

Reference implementation: `redrobin/cli_helpers/init_cmd.py::run_init`.

## Adding an adapter

1. Create `adapters/<name>/{__init__.py, adapter.py, manifest.toml}`. Copy the
   manifest shape from `adapters/cobaltstrike/manifest.toml`.
2. The adapter speaks only the wire protocol — see `protocol/schemas/v0.1/` and
   `.claude/rules/wire-protocol.md`.
3. Adapter tests in `adapters/<name>/tests/`.

## Code of conduct

Be respectful. We're building offensive security tooling — that comes with
ethical responsibility. Don't ship features that primarily enable
non-authorized targets.

## Claude Code

This repo ships committed Claude Code config under `.claude/` (path-scoped `rules/`, `skills/`) plus a
root `CLAUDE.md`. Run `/memory` inside a session to see which instructions and rules are loaded.
