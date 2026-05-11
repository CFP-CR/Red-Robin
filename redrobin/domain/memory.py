"""redrobin/domain/memory.py — Memory schemas per spec §4 Componente 3.

5 memory layers:
  L1 Long-term:    Fact (durable, cross-engagement)
  L2 Working:      WorkingMemory (hosts, accounts, defenses, tools, kill_chain) — per-engagement JSON
  L3 Episodic:     Lesson (engagement-specific) — SQLite
  L4 Tradecraft:   technique → bypass → detection graph — SQLite (deferred to v0.2 in v0.1)
  L5 Context:      sliding-window summary — in-memory + summary.txt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Host:
    name: str
    os: str | None = None
    integrity_level: str | None = None
    edr_detected: str | None = None
    access_level: str | None = None  # e.g., "user-token", "SYSTEM"


@dataclass
class Account:
    name: str  # e.g., "acme\\jdoe"
    kind: str  # "ntlm" | "kerberos" | "plaintext"
    source_host: str | None = None
    captured_at: str | None = None  # ISO timestamp


@dataclass
class ToolUsage:
    name: str  # e.g., "nanodump.bof"
    used_on: str  # host
    used_at: str  # ISO timestamp
    detection_observed: str = "none"


@dataclass
class Credential:
    """Credential observed on the wire (parsed from beacon output).

    Distinct from ``Account``: ``Account`` is operator-curated and may include
    plaintext/ticket material captured by hand; ``Credential`` is reducer-derived
    from event parsing and always carries a ``source_beacon_id`` so the operator
    can trace provenance.
    """

    domain: str
    username: str
    ntlm_hash: str | None = None
    source_beacon_id: str | None = None


@dataclass
class Finding:
    """Append-only observation produced by the reducer.

    ``kind`` is a short stable identifier (e.g., ``kerberos_ticket_observed``,
    ``opsec_block``). ``summary`` is a human-readable one-liner. Findings are
    never deduplicated or upserted — every occurrence is recorded.
    """

    kind: str
    summary: str
    source_beacon_id: str | None = None


@dataclass
class Session:
    """Live beacon session observed via c2.beacon_checkin events."""

    beacon_id: str
    hostname: str | None = None
    user: str | None = None
    integrity: str | None = None


@dataclass
class Objective:
    id: str
    goal: str
    status: str = "not_started"
    priority: str = "medium"  # low | medium | high
    progress_pct: int = 0
    milestones: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)


@dataclass
class WorkingMemory:
    engagement_id: str
    client: str = ""
    scope_in_scope: list[str] = field(default_factory=list)
    scope_out_of_scope: list[str] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)
    current_phase: str = "not_started"
    kill_chain_status: dict[str, str] = field(default_factory=dict)
    rules_of_engagement: list[str] = field(default_factory=list)
    hosts: list[Host] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    defenses_observed: list[str] = field(default_factory=list)
    tools_used: list[ToolUsage] = field(default_factory=list)
    # Slice 4: reducer-managed projections.
    credentials: list[Credential] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)


@dataclass(frozen=True)
class Lesson:
    """Episodic memory record — engagement-specific learning."""

    id: int | None  # None until persisted
    engagement_id: str
    text: str
    source: str  # "operator-tagged" | "auto-detected"
    created_at: datetime
    promoted_to_longterm: bool = False


@dataclass(frozen=True)
class Fact:
    """Long-term durable cross-engagement fact."""

    id: int | None  # None until persisted
    text: str
    source_engagement_id: str | None
    created_at: datetime
    embedding: list[float] | None = None


@dataclass(frozen=True)
class Episode:
    """Raw OPSEC verdict episode — one row per ``opsec.verdict`` event.

    Episodes are the audit trail the ``LessonPromoter`` aggregates over: when
    the same ``(command, verdict)`` pair recurs at or above threshold and the
    verdict is BLOCK or WARN, a single ``PromotedLesson`` is upserted into
    the long-term store so future LLM judgments can retrieve it from L1.
    """

    id: int | None  # None until persisted
    engagement_id: str
    correlation_id: str
    command: str
    verdict: str  # ALLOW | WARN | BLOCK | UNKNOWN
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class PromotedLesson:
    """L1 long-term record synthesised from recurring episodes.

    Distinct from ``Lesson`` (free-form engagement-specific learning stored in
    L3 Episodic): a ``PromotedLesson`` is a structured pattern record keyed by
    ``(command, verdict)`` and surfaced cross-engagement for future judgments.
    """

    id: int | None  # None until persisted
    command: str
    verdict: str  # BLOCK | WARN
    summary: str
    occurrence_count: int
    created_at: datetime
