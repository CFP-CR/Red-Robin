---
paths:
  - "protocol/**"
  - "redrobin/domain/events.py"
---

# Red Robin — Wire Protocol (canonical; verified against redrobin/domain/events.py)

The daemon speaks only the wire protocol; adapters translate C2 formats. Event dataclasses live in
`redrobin/domain/events.py` and mirror `protocol/schemas/v0.1/` (round-trip enforced by
`tests/unit/test_domain_events.py`). All 11 schemas are `additionalProperties: false`, so `/v1/events`
rejects unknown keys. Field names are canonical — match them exactly.

## The 10 events (+ Envelope)

| type | fields |
|---|---|
| `adapter.connected` | `manifest`, `session_id` |
| `adapter.heartbeat` | (none) |
| `beacon.initial` | `bid`, `host`, `user`, `integrity_level`, `arch`, `pid`, `ppid` |
| `beacon.command_input` | `bid`, `operator`, `command`, `raw`, `task_id?`, `correlation_id?` |
| `beacon.command_output` | `bid`, `task_id`, `output`, `exit_status?`, `correlation_id?` |
| `host.observation` | `bid`, `host_attr` |
| `credential.observed` | `bid`, `kind` (ntlm/kerberos/plaintext), `account`, `value_redacted_flag` |
| `operator.question` | `text`, `context_refs[]` |
| `operator.answer` | `question_id`, `text`, `citations[]` |
| `engagement.milestone` | `phase`, `milestone_id`, `status`, `progress_pct` |

Beacon events use **`bid`**, never `beacon_id`. `Envelope` = `{id, ts, source, event, spec_version,
extensions}`; `Envelope.to_dict()` drops `None` optionals.

## The one enum

- `Severity`: `INFO | WARN | DANGER | ESCALATE` (`redrobin/domain/events.py`). It is also the
  assistant's `Recommendation.opsec_risk` (`redrobin/domain/assistant.py`).

There is no `VerdictAction` — it went with the pre-flight gate (2026-07-26).

## Routes / ports

- `POST /v1/events` (+ `POST /v1/events/replay`) — the capture ingress; how the adapter feeds core.
  Bearer-protected.
- `POST /v1/adapters/{connect,heartbeat,disconnect}` — bearer-protected, session-bound.
- `GET /v1/adapter/heartbeat?adapter=<name>` — **singular, unauthenticated, stateless** liveness
  probe used by the `.cna`. Do not confuse it with the session-bound `POST` above.
- `WS /v1/tui` — daemon → TUI frames. Authenticated by a **`?token=` query param** (not a bearer
  header); mismatch closes with code 1008.
- Daemon default port **7777** (`REDROBIN_PORT`).

## Gotchas

- `/v1/events` MUST publish to the EventBus **after** JSONL persistence (persist-without-republish =
  adapter traffic never reaches the reducer; Phase-2 Slice-16 bug).
- The internal **bus** payload a subscriber sees may carry fields beyond the wire event (the router
  translates envelopes to bus shape). Verify in `events_router.py` before relying on them — they are
  NOT part of the wire contract.
