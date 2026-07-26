---
paths:
  - "redrobin/**"
---

# Red Robin — Architecture Rules

Layered Clean/DDD architecture; dependency direction enforced in CI by import-linter
(`importlinter.cfg`). Four contracts (do not violate):

1. **Layered Architecture**: `api | tui` → `application` → `domain`.
2. **Domain never imports infrastructure** (nor application/api/tui). `redrobin/domain` is pure.
3. **Application never imports presentation** (`api`/`tui`).
4. **Presentation never imports infrastructure directly (must go via application)** — `api`/`tui` reach
   infra only through an application facade (`allow_indirect_imports`).

## Layers on disk

`redrobin/{api, tui, application, domain, infrastructure}` + `cli.py` + `cli_helpers/`.

## Facade pattern

`api`/`tui` reach infrastructure only via application facades: `application/*_lifecycle.py`
(`cs_adapter_lifecycle`, `longterm_lifecycle`, `working_memory_lifecycle`) and
`application/transport_auth.py`. Never `from redrobin.infrastructure import ...` inside `api/` or `tui/`.

## Conventions

- **One ABC only**: `LLMProvider` (`redrobin/domain/llm.py`). Everything else concrete + duck typing.
- **mypy strict** runs only on `redrobin/domain` + `redrobin/application` (gradual in infrastructure).
- `.claude/rules/` holds Claude Code instructions (tooling). The top-level `rules/` Sigma/OPSEC pack
  was deleted with the pre-flight gate (2026-07-26) — there is no product `rules/` dir.
