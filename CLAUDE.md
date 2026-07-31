# Red Robin — Claude Code Instructions

Red Robin is an **operation-aware AI copilot behind your C2** for red-team operators: a Python
daemon + Textual TUI + Cobalt Strike adapter. It is **advisory-only**: the adapter captures the
operator's commands and their output (`beacon.command_input` / `beacon.command_output` /
`beacon.initial` → `POST /v1/events`) and the assistant advises in the TUI chat. Nothing intercepts
the command path. Core is C2-agnostic — **the adapter captures, the core advises**; adapters are the
sole C2 boundary.

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

- Non-trivial changes follow the AI-DLC — full order + phases in `.claude/rules/development-workflow.md`.
- **IMPORTANT: `.cna` changes are validated by a live-beacon operator smoke, NOT by CI** — CI never runs the `.cna`.
- TDD; atomic, conventional commits; **no AI co-author trailer** (see CONTRIBUTING).

## Layout & pointers

- Architecture: `docs/architecture-design.md` — how the system is shaped and why (explanation,
  not reference). The v1.2 design spec it replaced is in git history.
- Canonical wire API (event types, routes, enums, field names): `.claude/rules/wire-protocol.md`.
- Deeper dev walkthrough: `docs/developer-onboarding.md`.
- **`.claude/rules/`** = Claude Code path-scoped instructions (tooling). There is no product `rules/`
  dir any more — the top-level Sigma/OPSEC pack went with the pre-flight gate (2026-07-26).

<!-- Branching/PR flow lives in the imported CONTRIBUTING.md (single source of truth). Keep this file tight (<120 lines): move multi-step detail into .claude/rules/ or a skill, not here. -->

