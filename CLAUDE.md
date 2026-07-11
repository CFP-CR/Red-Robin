# Red Robin — Claude Code Instructions

Red Robin is an **operation-aware AI copilot behind your C2** for red-team operators: a Python
daemon + Textual TUI + Cobalt Strike adapter. The OPSEC pre-flight gate (CS Aggressor `alias`
override) is the first feature and the capture hook that feeds the assistant — not the whole
product. Core is C2-agnostic; **adapters are the sole C2 boundary**.

@CONTRIBUTING.md

## Commands

- Setup: `uv sync --all-extras`
- CI gates (all must pass — mirrors `.github/workflows/ci.yml`):
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy redrobin/domain redrobin/application`
  - `uv run lint-imports --config importlinter.cfg`
  - `uv run pytest tests/ -v --tb=short`
- Adapter tests are **separate** and NOT in the CI `pytest tests/` run — run them when you touch an
  adapter: `uv run pytest adapters/<name>/tests/`
- Local gotchas:
  - `uv run --no-sync ...` when the daemon is running (it locks `redrobin.exe`, so `uv run` re-sync fails).
  - `REDROBIN_ALLOW_DEGRADED=1` for tests that hit the boot-gate without API keys.

## Workflow

Superpowers methodology for any non-trivial change:
brainstorming → writing-plans → auditing-plans → subagent-driven-development → **operator smoke**.

- **`.cna` changes are validated by a live-beacon operator smoke, NOT by CI** — CI never runs the `.cna`.
- TDD, atomic commits, conventional messages, **no AI co-author trailer** (see CONTRIBUTING).

## Layout & pointers

- Architecture spec: `docs/architecture-design.md` (v1.2 design; some dates historical).
- Canonical wire API (event types, routes, enums, field names): `.claude/rules/wire-protocol.md`.
- Deeper dev walkthrough: `docs/developer-onboarding.md`.
- **Two unrelated "rules" dirs:** top-level `rules/` = Sigma/OPSEC Layer-A rules (product data);
  `.claude/rules/` = Claude Code path-scoped instructions (tooling).

## Branching (actual flow)

- `main` is protected (v0.1 shipped 2026-06-01). Do not push directly.
- `dev-juan` / `dev-jose` are operator integration branches.
- Topic branches off `dev-juan` (`feat-*`, `fix-*`, `docs-*`), merge back to `dev-juan`; `main` via PR at release.

<!-- Keep this file under ~120 lines. Move detail into .claude/rules/ (path-scoped) or a skill, not here. -->
