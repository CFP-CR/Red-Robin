# Database migrations

Red Robin uses **async Alembic** (`alembic init -t async`) because the runtime
driver is `aiosqlite`. The default sync template generates an `env.py` whose
`engine_from_config` call fails against an async driver.

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

`sqlalchemy.url` in `alembic.ini` points at a dev-only SQLite file
(`<repo>/.redrobin-dev.db`). Autogenerate diffs the live schema there against the
tables declared on the shared `metadata` in
`redrobin/infrastructure/persistence/db.py` — keep that module in step with any
schema change or autogenerate will not see it.

## This is a development tool, not a runtime step

The daemon does **not** run Alembic. Each repo drives `aiosqlite` directly with
hand-rolled DDL and creates or migrates its own tables on open, against:

| Path | Contents |
|---|---|
| `~/.redrobin/longterm.db` | cross-engagement durable facts |
| `~/.redrobin/context_summaries.db` | rolling context summaries |
| `~/.redrobin/engagements/<slug>/episodic.db` | lessons + one row per command, per engagement |

So a migration you generate here documents and validates the schema; it does not
ship to operators. Any change an existing operator database needs must also be
implemented in the owning repo's open path — see
`EpisodicRepo._migrate_episodes` for the pattern.
