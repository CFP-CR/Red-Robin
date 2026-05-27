"""redrobin/domain/events.py — Wire protocol event types (11 events + envelope).

Source: spec §4 Componente 7. Mirror of `protocol/schemas/v0.1/`. Round-trip with
the protocol schema is enforced by tests in `tests/unit/test_domain_events.py`.

NOTE: This is the canonical in-memory representation. Adapters and core code
exchange these dataclasses; serialization happens at HTTP/persistence boundaries
via `Envelope.to_dict()`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Union


class Severity(Enum):
    INFO = "INFO"
    WARN = "WARN"
    DANGER = "DANGER"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class Source:
    adapter: str
    version: str
    engagement_id: str
    adapter_session_id: str | None = None


@dataclass(frozen=True)
class AdapterConnectedEvent:
    manifest: dict[str, Any]
    session_id: str
    type: Literal["adapter.connected"] = "adapter.connected"


@dataclass(frozen=True)
class BeaconInitialEvent:
    bid: str
    host: str
    user: str
    integrity_level: str
    arch: str
    pid: int
    ppid: int
    type: Literal["beacon.initial"] = "beacon.initial"


@dataclass(frozen=True)
class BeaconCommandInputEvent:
    bid: str
    operator: str
    command: str
    raw: str
    task_id: str | None = None
    correlation_id: str | None = None
    type: Literal["beacon.command_input"] = "beacon.command_input"


@dataclass(frozen=True)
class BeaconCommandOutputEvent:
    bid: str
    task_id: str
    output: str
    exit_status: int | None = None
    correlation_id: str | None = None
    type: Literal["beacon.command_output"] = "beacon.command_output"


@dataclass(frozen=True)
class HostObservationEvent:
    bid: str
    host_attr: dict[str, Any]
    type: Literal["host.observation"] = "host.observation"


@dataclass(frozen=True)
class CredentialObservedEvent:
    bid: str
    kind: Literal["ntlm", "kerberos", "plaintext"]
    account: str
    value_redacted_flag: bool
    type: Literal["credential.observed"] = "credential.observed"


@dataclass(frozen=True)
class OpsecVerdictEvent:
    source_event_id: str
    severity: Severity
    reason: str
    rule_id: str | None = None
    suggestions: list[str] = field(default_factory=list)
    type: Literal["opsec.verdict"] = "opsec.verdict"


@dataclass(frozen=True)
class OperatorQuestionEvent:
    text: str
    context_refs: list[str] = field(default_factory=list)
    type: Literal["operator.question"] = "operator.question"


@dataclass(frozen=True)
class OperatorAnswerEvent:
    question_id: str
    text: str
    citations: list[str] = field(default_factory=list)
    type: Literal["operator.answer"] = "operator.answer"


@dataclass(frozen=True)
class EngagementMilestoneEvent:
    phase: str
    milestone_id: str
    status: str
    progress_pct: int
    type: Literal["engagement.milestone"] = "engagement.milestone"


@dataclass(frozen=True)
class AdapterHeartbeatEvent:
    type: Literal["adapter.heartbeat"] = "adapter.heartbeat"


EventPayload = Union[
    AdapterConnectedEvent,
    BeaconInitialEvent,
    BeaconCommandInputEvent,
    BeaconCommandOutputEvent,
    HostObservationEvent,
    CredentialObservedEvent,
    OpsecVerdictEvent,
    OperatorQuestionEvent,
    OperatorAnswerEvent,
    EngagementMilestoneEvent,
    AdapterHeartbeatEvent,
]


@dataclass
class Envelope:
    id: str
    ts: datetime
    source: Source
    event: EventPayload
    spec_version: str = "0.1.0"
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        # Serialize Severity enum to string when present (OpsecVerdictEvent)
        if isinstance(self.event, OpsecVerdictEvent):
            d["event"]["severity"] = self.event.severity.value
        # Drop None for optional adapter_session_id
        if self.source.adapter_session_id is None:
            d["source"].pop("adapter_session_id", None)
        # Drop None for optional task_id on input events
        if isinstance(self.event, BeaconCommandInputEvent) and self.event.task_id is None:
            d["event"].pop("task_id", None)
        # Drop None for optional exit_status on output events (schema allows null OR omit)
        if isinstance(self.event, BeaconCommandOutputEvent) and self.event.exit_status is None:
            d["event"].pop("exit_status", None)
        # Drop None for optional correlation_id on command input/output events
        if (
            isinstance(self.event, (BeaconCommandInputEvent, BeaconCommandOutputEvent))
            and self.event.correlation_id is None
        ):
            d["event"].pop("correlation_id", None)
        # Drop None for optional rule_id on verdict events
        if isinstance(self.event, OpsecVerdictEvent) and self.event.rule_id is None:
            d["event"].pop("rule_id", None)
        return d
