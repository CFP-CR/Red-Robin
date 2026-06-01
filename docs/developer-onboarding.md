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
│   ├── domain/              # business logic (events, opsec, rules, memory schemas) — mypy strict
│   ├── application/         # use cases / orchestrators (Layer A, Layer B, verdict pipeline) — mypy strict
│   ├── infrastructure/      # I/O concretes (DB, HTTP, LLM clients, filesystem) — mypy gradual
│   ├── api/                 # FastAPI routes (HTTP entry points)
│   └── tui/                 # prompt_toolkit TUI (panels, slash commands, inspector)
├── protocol/                # wire protocol v0.1 (schemas + fixtures + validator)
├── adapters/                # C2 adapters
│   ├── cobaltstrike/        # CS adapter (.cna + Python connector)
│   └── sliver-mock/         # synthetic Sliver event replayer
├── rules/                   # YAML rule packs (Layer A bootstrap + passthrough whitelist)
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

In another terminal:

```bash
# Send a verdict request (Path 1 simulation)
curl -X POST http://127.0.0.1:7777/v1/verdict/quick \
  -H "Authorization: Bearer $(cat ~/.redrobin/transport.token)" \
  -H "Content-Type: application/json" \
  -d '{"bid": "b1", "command": "mimikatz", "raw": "mimikatz logonpasswords"}'
```

Expected: BLOCK verdict if bootstrap rules are loaded.

## Where do I start?

| If you want to... | Open this |
|---|---|
| Add a rule | `rules/shared/<new_rule>.yaml` |
| Add an adapter | `adapters/<new_adapter>/` |
| Touch the verdict pipeline | `redrobin/application/verdict_pipeline.py` + `verdict_pipeline_factory.py` |
| Add a TUI panel | `redrobin/tui/panels/` |
| Add a CLI command | `redrobin/cli.py` |
| Add a new event type | `protocol/schemas/v0.1/events/` + `redrobin/domain/events.py` + fixtures + tests |
| Debug a Layer B verdict | `redrobin/application/layer_b_engine.py` |
| Tweak the .cna alias gate | `adapters/cobaltstrike/redrobin.cna` + `adapters/cobaltstrike/aliases/*.sleep` |

## Common gotchas

- **`import-linter` failures**: you imported from `infrastructure` in
  `api/` or `tui/`. Fix: route through an `application/` facade (see existing
  `application/transport_auth.py`, `application/event_ingestion.py`).
- **mypy strict failure on domain**: domain code must be fully typed.
  `infrastructure` is gradual.
- **Aggressor alias `$2` is the first token**, not the full arg string. Use
  the canonical `substr($0, strlen("<cmd> ")+1)` pattern (see Phase 1 spike
  validation in `docs/aggressor-alias-args.md`).
- **API key in config.toml**: programmatically refused at startup. Use env
  vars only.

## Ask questions

- Slack/Discord: TBD
- GitHub Issues: `https://github.com/CFP-CR/Red-Robin/issues`
- Architecture/spec questions: tag spec discussion issues with `architecture`.
