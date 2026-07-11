---
paths:
  - "tests/**"
  - "adapters/**/tests/**"
---

# Red Robin — Testing Rules

- **TDD cadence**: failing test → verify red → minimal implementation → verify green → commit.
- **Layout**: `tests/unit/` (pure), `tests/integration/` (HTTP/integration), `adapters/<name>/tests/`
  (adapter — NOT run by CI's `pytest tests/`; run explicitly when touching an adapter).
- **Canonical enums in assertions**: `VerdictAction` = PASS/WARN/BLOCK/ESCALATE (**no `ALLOW`**);
  `Severity` = INFO/WARN/DANGER/ESCALATE.
- **Local gotchas**: `uv run --no-sync ...` when the daemon runs (locked `redrobin.exe`);
  `REDROBIN_ALLOW_DEGRADED=1` for boot-gated tests.
- Integration tests of the async reducer read `app.state.working_memory_repo` and poll — not a
  hardcoded working-memory path.
- mypy strict covers only `redrobin/domain` + `redrobin/application`.
