---
paths:
  - "protocol/**"
  - "redrobin/domain/events.py"
---

# Red Robin — Wire Protocol (canonical; verified against redrobin/domain/events.py)

The daemon speaks only the wire protocol; adapters translate C2 formats. Event dataclasses live in
`redrobin/domain/events.py` and mirror `protocol/schemas/v0.1/` (round-trip enforced by
`tests/unit/test_domain_events.py`). All 12 schemas are `additionalProperties: false`, so `/v1/events`
rejects unknown keys. Field names are canonical — match them exactly.

## The 11 events (+ Envelope)

| type | fields |
|---|---|
| `adapter.connected` | `manifest`, `session_id` |
| `adapter.heartbeat` | (none) |
| `beacon.initial` | `bid`, `host`, `user`, `integrity_level`, `arch`, `pid`, `ppid` |
| `beacon.command_input` | `bid`, `operator`, `command`, `raw`, `task_id?`, `correlation_id?` |
| `beacon.command_output` | `bid`, `task_id`, `output`, `exit_status?`, `correlation_id?` |
| `host.observation` | `bid`, `host_attr` |
| `credential.observed` | `bid`, `kind` (ntlm/kerberos/plaintext), `account`, `value_redacted_flag` |
| `opsec.verdict` | `source_event_id`, `severity`, `reason`, `rule_id?`, `suggestions[]` |
| `operator.question` | `text`, `context_refs[]` |
| `operator.answer` | `question_id`, `text`, `citations[]` |
| `engagement.milestone` | `phase`, `milestone_id`, `status`, `progress_pct` |

Beacon events use **`bid`**, never `beacon_id`. `Envelope` = `{id, ts, source, event, spec_version,
extensions}`; `Envelope.to_dict()` drops `None` optionals.

## Two enums — do NOT conflate

- `Severity` (on events): `INFO | WARN | DANGER | ESCALATE`.
- `VerdictAction` (verdict pipeline / `/v1/verdict/quick` response): `PASS | WARN | BLOCK | ESCALATE`.
  **There is no `ALLOW`** (only a legacy fallback string in episodic records).

## Routes / ports

- Quick verdict: `POST /v1/verdict/quick` (bearer-protected; router `prefix="/v1/verdict"`).
- CS dispatch (adapter side): `POST /api/v1/beacons/{bid}/consoleCommand`.
- Daemon default port **7777** (`REDROBIN_PORT`).

## Gotchas

- `/v1/events` MUST publish to the EventBus **after** JSONL persistence (persist-without-republish =
  adapter traffic never reaches the reducer; Phase-2 Slice-16 bug).
- The internal **bus** verdict payload (what the reducer consumes) may add fields beyond the wire
  `OpsecVerdictEvent` (e.g. a beacon id, `source=layer_a|layer_b`). Verify in `verdict_pipeline.py`
  before relying on them — they are NOT on the wire event.
