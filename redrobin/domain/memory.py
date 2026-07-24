"""redrobin/domain/memory.py — Memory schemas per spec §4 Componente 3.

5 memory layers:
  L1 Long-term:    PromotedLesson (durable, cross-engagement, structured)
  L2 Working:      WorkingMemory (hosts, defenses, tools, kill_chain) — per-engagement JSON
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
    domain: str | None = None  # AD domain (systeminfo "Domain:") — drives AD reasoning
    ip_addresses: list[str] = field(default_factory=list)  # NIC IPs — lateral movement
    os_version: str | None = None  # build/patch level — exploit selection


@dataclass
class ToolUsage:
    name: str  # e.g., "nanodump.bof"
    used_on: str  # host
    used_at: str  # ISO timestamp
    detection_observed: str = "none"


@dataclass
class Credential:
    """Credential observed on the wire (parsed from beacon output).

    Reducer-derived from event parsing; always carries a ``source_beacon_id`` so
    the operator can trace provenance.

    ``secret_type`` is an OPEN vocabulary (not an Enum) so non-AD material
    (aws_key, azure_token, ...) needs no schema change. ``ntlm_hash`` is retained
    for back-compat with working_memory.json written before this generalization.
    New NTLM material flows into ``secret``/``secret_type`` via the LLM enricher
    (Plan B2 retired the regex producer); ``ntlm_hash`` removal is a separate
    follow-up once historic WM files are migrated.
    """

    domain: str
    username: str
    ntlm_hash: str | None = None
    secret_type: str = "ntlm"  # ntlm | plaintext | aes256 | dcc2 | kerberos_ticket | aws_key | ...
    secret: str | None = None  # the captured material (hash/password/key/ticket ref)
    source_beacon_id: str | None = None


@dataclass
class Finding:
    """Observation produced by the enrichment/reducer path.

    ``kind`` is a short stable identifier (e.g., ``kerberos_ticket_observed``,
    ``opsec_block``). ``summary`` is a human-readable one-liner. In the deltas
    merge (``WorkingMemoryRepo.apply_deltas``) findings are deduped by
    ``(kind, summary, source_beacon_id)`` so re-emission / overlapping chunks
    do not pile up duplicates. ``append_finding`` is kept for the BLOCK-verdict
    audit stream (``_on_verdict`` writes through it; no dedup — every BLOCK
    matters); ``apply_deltas`` is the dedup'd path for enrichment-derived
    findings (since Plan B2 retired the regex producer, that is the only other
    write path).
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
class PendingMilestone:
    """A close/commit interpretation the assistant proposed and is awaiting the
    operator's binary confirm (spec §6.C). Persisted in WM so continuity survives
    a restart — the assistant holds NO live pending state. Read back by id on the
    operator's answer; rendered by the confirm affordance (deferred UI, #28)."""

    milestone_id: str
    phase: str
    status: str
    progress_pct: int
    prompt: str


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
    defenses_observed: list[str] = field(default_factory=list)
    tools_used: list[ToolUsage] = field(default_factory=list)
    # Slice 4: reducer-managed projections.
    credentials: list[Credential] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    pending_milestones: list[PendingMilestone] = field(default_factory=list)


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
class Lesson:
    """L1 operator-correction lesson (spec §6.D) — a context-scoped, NEVER-blanket
    record captured when the operator corrects the assistant. Distinct from
    ``PromotedLesson`` (the verdict-aggregation record keyed by command/verdict):
    this is the operator-correction lesson-source (§10). ``scope`` is
    ``"operator"`` (persists cross-engagement — cumulative) or ``"engagement"``
    (quarantined — read back only for its own engagement, so a honeypot fact never
    contaminates the next client). Provenance (``engagement_id``, ``created_at``)
    is system-attached at capture, never model-supplied."""

    scope: str  # "operator" | "engagement"
    context: str  # the situation it applies in
    lesson: str  # what to do differently
    why: str  # the reason
    engagement_id: str | None  # provenance + the quarantine key
    created_at: datetime
    id: int | None = None  # None until persisted


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


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, (str, int, float))]


@dataclass
class WorkingMemoryDeltas:
    """A batch of facts extracted from one beacon command output.

    Produced by the enrichment path (Plan B) and merged into WorkingMemory by
    the reducer via :meth:`WorkingMemoryRepo.apply_deltas`. ``from_payload``
    whitelists keys (unknown keys are dropped — the LLM cannot inject fields)
    and system-attaches ``source_beacon_id`` to credentials/findings so
    provenance is never LLM-supplied. Only output-derived fact types appear
    here; objectives/phase/kill-chain (interpretation) and sessions/scope
    (other event sources) are intentionally absent.
    """

    hosts: list[Host] = field(default_factory=list)
    credentials: list[Credential] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    tools_used: list[ToolUsage] = field(default_factory=list)
    defenses_observed: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any, *, source_beacon_id: str | None) -> WorkingMemoryDeltas:
        if not isinstance(payload, dict):
            return cls()
        hosts: list[Host] = []
        for h in payload.get("hosts") or []:
            if not isinstance(h, dict) or not h.get("name"):
                continue
            hosts.append(
                Host(
                    name=str(h["name"]),
                    os=_opt_str(h.get("os")),
                    integrity_level=_opt_str(h.get("integrity_level")),
                    edr_detected=_opt_str(h.get("edr_detected")),
                    access_level=_opt_str(h.get("access_level")),
                    domain=_opt_str(h.get("domain")),
                    ip_addresses=_str_list(h.get("ip_addresses")),
                    os_version=_opt_str(h.get("os_version")),
                )
            )
        credentials: list[Credential] = []
        for c in payload.get("credentials") or []:
            if not isinstance(c, dict) or not c.get("username"):
                continue
            secret = _opt_str(c.get("secret"))
            ntlm_hash = _opt_str(c.get("ntlm_hash"))
            # #114: a credential with no secret material is just a username
            # observation (the enricher sometimes emits the logged-in user). Drop
            # it — CREDENTIALS is for usable secrets, not bare usernames.
            if not secret and not ntlm_hash:
                continue
            credentials.append(
                Credential(
                    domain=str(c.get("domain", "")),
                    username=str(c["username"]),
                    ntlm_hash=ntlm_hash,
                    secret_type=str(c.get("secret_type") or "ntlm"),
                    secret=secret,
                    source_beacon_id=source_beacon_id,
                )
            )
        findings: list[Finding] = []
        for f in payload.get("findings") or []:
            if not isinstance(f, dict) or not f.get("kind"):
                continue
            findings.append(
                Finding(
                    kind=str(f["kind"]),
                    summary=str(f.get("summary", "")),
                    source_beacon_id=source_beacon_id,
                )
            )
        tools_used: list[ToolUsage] = []
        now_iso = datetime.now().isoformat()
        for t in payload.get("tools_used") or []:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            tools_used.append(
                ToolUsage(
                    name=str(t["name"]),
                    used_on=str(t.get("used_on", "")),
                    used_at=str(t.get("used_at") or now_iso),
                    detection_observed=str(t.get("detection_observed", "none")),
                )
            )
        return cls(
            hosts=hosts,
            credentials=credentials,
            findings=findings,
            tools_used=tools_used,
            defenses_observed=_str_list(payload.get("defenses_observed")),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "hosts": [asdict(h) for h in self.hosts],
            "credentials": [asdict(c) for c in self.credentials],
            "findings": [asdict(f) for f in self.findings],
            "tools_used": [asdict(t) for t in self.tools_used],
            "defenses_observed": list(self.defenses_observed),
        }


def working_memory_to_snapshot(wm: WorkingMemory) -> dict[str, Any]:
    """Serialize a :class:`WorkingMemory` to the WS ``working_memory_snapshot``
    payload dict (consumed directly by the Textual TUI client).

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
