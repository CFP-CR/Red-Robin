# Developer onboarding

This is the 30-minute tour of Red Robin for a developer joining the project.

## Read these first

1. `Projects/red-robin-architecture-design.md` (spec, ~2600 lines) — start
   with §1 (Mission), §3 (System Topology), §4 (Component Architecture).
2. `Projects/red-robin-v0.1-implementation-plan.md` (plan) — see which tasks
   are open + their dependencies.

## Repo tour

```
Red-Robin/
├── redrobin/                # main package
│   ├── domain/              # business logic (events, assistant, chat, memory schemas) — mypy strict
│   ├── application/         # use cases / orchestrators (assistant agent, enricher, WM reducer) — mypy strict
│   ├── infrastructure/      # I/O concretes (DB, HTTP, LLM clients, filesystem) — mypy gradual
│   ├── api/                 # FastAPI routes (HTTP entry points)
│   └── tui/                 # Textual TUI (textual_ui/: panels, chat, inspector)
├── protocol/                # wire protocol v0.1 (schemas + fixtures + validator)
├── adapters/                # C2 adapters
│   ├── cobaltstrike/        # CS adapter (.cna capture hook + Python REST connector)
│   └── sliver-mock/         # synthetic Sliver event replayer
├── migrations/              # async Alembic migrations
├── tests/                   # unit + integration tests
└── docs/                    # human-facing docs
```

## Run the test suite

```bash
uv run pytest tests/ adapters/cobaltstrike/tests/
uv run pytest adapters/sliver-mock/tests/  # separate run — different `tests` package
```

## Run the daemon locally

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export VOYAGE_API_KEY="pa-..."
uv run redrobin init
uv run redrobin daemon start &
uv run redrobin doctor
```

In another terminal, hand-feed the daemon a captured event — the same thing
the `.cna` does for real:

```bash
curl -X POST http://127.0.0.1:7777/v1/events \
  -H "Authorization: Bearer $(cat ~/.redrobin/transport.token)" \
  -H "Content-Type: application/json" \
  -d '{
    "spec_version": "0.1.0",
    "id": "evt_01HZXAB9MK2P6Q9TFGHJKLMNPQ",
    "ts": "2026-07-26T14:32:00.000Z",
    "source": {"adapter": "cobaltstrike", "version": "0.1.0", "engagement_id": "op-smoke"},
    "event": {
      "type": "beacon.command_input",
      "bid": "bid_4291",
      "operator": "alice",
      "command": "shell",
      "raw": "shell whoami /priv"
    }
  }'
```

Expected: `{"ok": true, "stored": true}` (`stored` is `false` when no
engagement is active). The envelope is validated against
`protocol/schemas/v0.1/`, appended to the engagement's `events.jsonl`, and
republished on the event bus, so the reducer and the TUI both see it. Fixtures
for every event type live in `protocol/fixtures/v0.1/valid/`.

## Where do I start?

| If you want to... | Open this |
|---|---|
| Add an adapter | `adapters/<new_adapter>/` |
| Change what the assistant says | `redrobin/application/agents/assistant/` (`agent.py` prompt, `triggers.py` speak/stay-quiet gate, `tools.py` KB + journal retrieval) |
| Change what lands in working memory | `redrobin/application/working_memory_reducer.py` + `redrobin/application/agents/enricher/` |
| Add a TUI panel | `redrobin/tui/textual_ui/panels.py` (+ `theme.tcss`) |
| Add a CLI command | `redrobin/cli.py` + `redrobin/cli_helpers/` |
| Add a new event type | `protocol/schemas/v0.1/events/` + `redrobin/domain/events.py` + fixtures + tests |
| Tweak what the .cna captures | `adapters/cobaltstrike/redrobin.cna` (`on beacon_input`, `set BEACON_OUTPUT`, `on beacon_initial`) |

## Common gotchas

- **`import-linter` failures**: you imported from `infrastructure` in
  `api/` or `tui/`. Fix: route through an `application/` facade (see existing
  `application/transport_auth.py`, `application/event_ingestion.py`).
- **mypy strict failure on domain**: domain code must be fully typed.
  `infrastructure` is gradual.
- **The `.cna` must stay passthrough.** It is a capture hook, not a gate:
  no `alias` overrides, and `BEACON_OUTPUT` always returns `$2` unchanged.
  Anything that can throw before that return suppresses the operator's
  output — wrap it.
- **CI never runs the `.cna`.** Aggressor changes are accepted by a live-beacon
  operator smoke, not by the test suite. See `.claude/skills/smoke-test/`.
- **API key in config.toml**: programmatically refused at startup. Use env
  vars only.
- **The assistant is off by default** (`REDROBIN_ASSISTANT_ENABLED=1` to
  opt in), so a daemon that captures happily but never speaks is usually
  configuration, not a bug.

## Ask questions

- Slack/Discord: TBD
- GitHub Issues: `https://github.com/CFP-CR/Red-Robin/issues`
- Architecture/spec questions: tag spec discussion issues with `architecture`.
