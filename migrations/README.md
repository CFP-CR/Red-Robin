# Database Migrations

Red Robin uses **async Alembic** (template `-t async`) to support `aiosqlite`.
The default sync template (`alembic init migrations`) generates an `env.py` that
fails at runtime with `aiosqlite` because `engine_from_config` is synchronous.

## Creating a new migration

```bash
uv run alembic revision --autogenerate -m "describe change"
```

## Applying migrations

```bash
uv run alembic upgrade head
```

## Runtime DBs vs. dev DB

The `sqlalchemy.url` in `alembic.ini` points at a dev-time SQLite file
(`<repo>/.redrobin-dev.db`) used only for autogenerate diff and manual upgrade
testing. At runtime the daemon constructs engines pointed at:

- `~/.redrobin/shared/longterm.db` (cross-engagement facts)
- `~/.redrobin/shared/tradecraft.db` (technique graph)
- `~/.redrobin/engagements/<slug>/episodic.db` (per-engagement lessons)

Migrations are applied to each of those at daemon startup via the same
`migrations/` directory, configured per-engine in
`redrobin/infrastructure/persistence/db.py` (Task 23+).
