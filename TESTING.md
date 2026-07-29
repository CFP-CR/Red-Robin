# Testing

Red Robin has two kinds of acceptance, and which one applies depends entirely on
what you touched.

| What you changed | How it is accepted |
|---|---|
| Deterministic logic — daemon, reducer, assistant plumbing, CLI, protocol | The CI gates below. |
| The live surface — the `.cna`, the enricher/reducer over real Beacon output, the TUI | A live-Beacon operator smoke. CI cannot exercise these. |

## CI gates

All six must pass. This mirrors `.github/workflows/ci.yml` exactly.

```bash
uv sync --all-extras

uv run ruff check .
uv run ruff format --check .
uv run mypy redrobin/domain redrobin/application
uv run lint-imports --config importlinter.cfg
uv run pytest tests/ -v --tb=short
uv run pytest adapters/ -v --tb=short
```

CI runs on Python 3.11. `pytest tests/` and `pytest adapters/` are two separate
steps — `adapters/` is **not** collected by the first, so run it explicitly
whenever you touch an adapter.

Two local gotchas:

- **`uv run --no-sync ...` while a daemon is running.** `uv` re-syncs before
  running and cannot replace the locked `redrobin.exe`, failing with `os error 32`.
- **Run pytest from a directory with no `.env`** — for example
  `<checkout>/tools`. A `.env` in the working directory is loaded by the CLI code
  under test and can mask real failures.

## Live-Beacon smokes

These are procedures, not test suites. Each is the acceptance artifact for its
surface; there is no offline Sleep linter and CI never loads the `.cna`.

- [`adapters/cobaltstrike/tests/test_cna_smoke.md`](adapters/cobaltstrike/tests/test_cna_smoke.md)
  — the adapter: no interception, native argument handling, capture reaches the
  daemon, transport failure modes.
- [`scripts/smoke/rr-assistant-smoke-runbook.md`](scripts/smoke/rr-assistant-smoke-runbook.md)
  — the assistant surface: advisories, proactive mode, the confirm affordance,
  conversational Q&A over a real Beacon.
- `.claude/skills/smoke-test/` — the procedure wrapper.

## Verifying without Cobalt Strike

To check the whole daemon-side chain — ingestion, validation, persistence,
reducer, TUI, assistant — with no Beacon and no CS licence, use
[`docs/smoke-test.md`](docs/smoke-test.md). It drives the capture path with the
Sliver-Mock replayer and takes about five minutes.

## Writing tests

Layout and conventions are in [CONTRIBUTING.md](CONTRIBUTING.md#tests). In short:
`tests/unit/` for pure logic, `tests/integration/` for HTTP, `tests/e2e/` for
whole-flow, `adapters/<name>/tests/` for adapters. All new code needs tests.
