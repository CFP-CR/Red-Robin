"""redrobin/domain/memory.py — Memory schemas per spec §4 Componente 3.

5 memory layers:
  L1 Long-term:    PromotedLesson (durable, cross-engagement, structured)
  L2 Working:      WorkingMemory (hosts, accounts, defenses, tools, kill_chain) — per-engagement JSON
  L3 Episodic:     Episode (raw verdict log, per-engagement) — SQLite
  L4 Tradecraft:   technique → bypass → detection graph — SQLite (deferred to v0.2 in v0.1)
  L5 Context:      sliding-window ContextSummary — in-memory + summary.txt

Slice 12 removed the never-used ``Lesson`` (free-form L3 episodic record) and
``Fact`` (free-form L1 long-term record) dataclasses. The promotion path
described in spec §4 always emits structured ``PromotedLesson`` rows in v0.1;
free-form variants will return in v0.2 if/when the LessonPromoter grows a
free-text branch.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


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
    """Live beacon session observed via beacon.initial events.

    Field naming on this dataclass predates the canonical wire schema;
    ``beacon_id`` corresponds to wire ``bid``, ``hostname`` to wire ``host``,
    and ``integrity`` to wire ``integrity_level``. The reducer performs the
    rename at the boundary.
    """

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

    Structured pattern record keyed by ``(command, verdict)`` and surfaced
    cross-engagement for future judgments. Produced by ``LessonPromoter``
    when ``EpisodicRepo`` shows a verdict pattern recurring at or above the
    configured ``REDROBIN_PROMOTER_MIN_OCCURRENCES`` threshold.
    """

    id: int | None  # None until persisted
    command: str
    verdict: str  # BLOCK | WARN
    summary: str
    occurrence_count: int
    created_at: datetime


@dataclass(frozen=True)
class ContextSummary:
    """L5 sliding-window summary — engagement-specific compression of recent episodes.

    Produced periodically by ``L5Summarizer`` once enough new episodes have
    accumulated (``window_size``). Persisted in :class:`ContextSummaryRepo`
    and surfaced as a cached system block in Layer B's prompt so long
    engagements stay coherent after the most recent verdicts roll off the
    in-memory window.
    """

    engagement_id: str
    text: str
    window_size: int
    created_at: datetime = field(default_factory=datetime.now)


def working_memory_to_snapshot(wm: WorkingMemory) -> dict[str, Any]:
    """Serialize a :class:`WorkingMemory` to the WS ``working_memory_snapshot``
    payload dict — the inverse of
    ``redrobin.tui.snapshot_adapter.working_memory_from_snapshot``.

    Single source of truth for BOTH the connect-time snapshot
    (``tui_ws._build_initial_snapshot``) and the reducer's live re-emit
    (#146). ``asdict`` emits every field with dataclass field names as keys
    (nested dataclasses → nested dicts), which is exactly what the adapter's
    ``.get()`` calls read; ``repo.save()`` already persists via ``asdict``.
    Lives in ``domain`` so the reducer (application) and tui_ws (api) can both
    import it without crossing the presentation-layer boundary.

    PRECONDITION: every WorkingMemory field must stay JSON-native (str/int/
    list/dict) — the result is sent over the TUI WebSocket via ``send_json``,
    which encodes without a ``default=`` fallback. A future ``datetime`` field
    would persist to disk (repo.save uses ``default=str``) but raise at WS send.
    """
    return asdict(wm)
