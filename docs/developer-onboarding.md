# Developer onboarding

The 30-minute tour for a developer joining Red Robin. Get the operator setup
working first — [operator-setup.md](operator-setup.md) — then come back here.

## Repo tour

```
Red-Robin/
├── redrobin/                # main package
│   ├── domain/              # business logic (events, assistant, chat, memory schemas) — mypy strict
│   ├── application/         # use cases / orchestrators (assistant agent, enricher, WM reducer) — mypy strict
│   ├── infrastructure/      # I/O concretes (DB, HTTP, LLM clients, filesystem) — mypy gradual
│   ├── api/                 # FastAPI routes (HTTP entry points)
│   ├── tui/                 # Textual TUI (textual_ui/: panels, chat, inspector)
│   ├── cli.py               # argparse + dispatch only
│   └── cli_helpers/         # one module per subcommand body
├── protocol/                # wire protocol v0.1 (schemas + fixtures + validator)
├── adapters/                # C2 adapters
│   ├── cobaltstrike/        # the .cna capture hook (capture-only, no dispatch)
│   └── sliver-mock/         # synthetic Sliver event replayer
├── migrations/              # async Alembic migrations
├── tests/                   # unit + integration + e2e
└── docs/                    # human-facing docs
```

Architecture rules and the layering contracts CI enforces are in
[CONTRIBUTING.md](../CONTRIBUTING.md). The canonical wire API — event types,
routes, enums, field names — is `.claude/rules/wire-protocol.md`. The v1.2 design
spec is `architecture-design.md`, kept as a historical record: it predates the
removal of the pre-flight gate and describes behaviour that no longer exists.

## Run the tests

```bash
uv run pytest tests/       # the CI suite
uv run pytest adapters/    # adapter tests — a separate CI step
```

Add `--no-sync` to any `uv run` while a daemon is running; `uv` cannot replace
the locked `redrobin.exe` otherwise.

## Feed the daemon an event by hand

The same thing the `.cna` does for real:

```bash
curl -X POST http://127.0.0.1:7777/v1/events \
  -H "Authorization: Bearer $(cat ~/.redrobin/transport.token)" \
  -H "Content-Type: application/json" \
  -d '{
    "spec_version": "0.1.0",
    "id": "evt_01HZXAB9MK2P6Q9TFGHJKRMNPQ",
    "ts": "2026-07-28T14:32:00.000Z",
    "source": {"adapter": "cobaltstrike", "version": "0.1.0", "engagement_id": "acme-q2-2026"},
    "event": {
      "type": "beacon.command_input",
      "bid": "bid_4291",
      "operator": "alice",
      "command": "shell",
      "raw": "shell whoami /priv"
    }
  }'
```

Expected: `{"ok":true,"stored":true}` — `stored` is `false` when no engagement is
active. The envelope is validated against `protocol/schemas/v0.1/`, appended to
the engagement's `events.jsonl`, and republished on the event bus so the reducer
and the TUI both see it. Valid fixtures for every event type live in
`protocol/fixtures/v0.1/valid/` — build your payloads from those; the schemas are
`additionalProperties: false`, so any extra key is a hard reject.

Liveness: `GET /v1/healthz` returns `{"status":"ok"}` immediately;
`GET /v1/readyz` returns `{"status":"ready"}` only once the lifespan has finished
booting.

## Where do I start?

| If you want to… | Open this |
|---|---|
| Add an adapter | `adapters/<new_adapter>/` |
| Change what the assistant says | `redrobin/application/agents/assistant/` (`agent.py` prompt, `triggers.py` speak/stay-quiet gate, `tools.py` KB + journal retrieval) |
| Change what lands in working memory | `redrobin/application/working_memory_reducer.py` + `redrobin/application/agents/enricher/` |
| Add a TUI panel | `redrobin/tui/textual_ui/panels.py` (+ `theme.tcss`) |
| Add a CLI command | `redrobin/cli.py` + `redrobin/cli_helpers/` |
| Add a new event type | `protocol/schemas/v0.1/events/` + `redrobin/domain/events.py` + fixtures + tests |
| Change what the `.cna` captures | `adapters/cobaltstrike/redrobin.cna` (`on beacon_input`, `set BEACON_OUTPUT`, `on beacon_initial`) |
| Change how the daemon boots | `redrobin/api/lifespan_helpers.py` |

## Common gotchas

- **`import-linter` failures.** You imported `infrastructure` from `api/` or
  `tui/`. Route it through an `application/` facade — see
  `application/transport_auth.py`, `application/event_ingestion.py`.
- **mypy strict on domain.** `domain` and `application` must be fully typed;
  `infrastructure` is gradual.
- **The `.cna` must stay passthrough.** It is a capture hook, not a gate: no
  `alias` overrides, and `BEACON_OUTPUT` always returns `$2` unchanged. Anything
  that can throw before that return suppresses the operator's output — wrap it.
- **CI never runs the `.cna`.** Aggressor changes are accepted by a live-Beacon
  operator smoke. See `.claude/skills/smoke-test/`.
- **Engagement state binds at daemon startup.** Working memory, episodic history
  and the assistant's scope are resolved once, in the lifespan. `events.jsonl`
  is resolved per request. Switching engagements under a running daemon splits
  the two — see
  [configuration.md](configuration.md#switching-engagements).
- **A daemon that captures but never speaks** is usually configuration:
  `REDROBIN_ASSISTANT_ENABLED` or a key that is `set` but not *valid*.

## Ask questions

- GitHub Issues: <https://github.com/CFP-CR/Red-Robin/issues>
- Tag spec discussions with `architecture`.
