"""Convert a daemon ``working_memory_snapshot`` dict into a typed ``WorkingMemory``.

The daemon's WebSocket frame carries a JSON-serialized projection of the live
``WorkingMemoryRepo``; the TUI's rich panel renderers (``panels/working_memory.py``,
``inspector.py``) expect a domain ``WorkingMemory`` dataclass instance. This
adapter performs the field-by-field rehydration so the rich renderers can
consume snapshot frames without the panel modules being coupled to the WS shape.

Field coverage matches what ``redrobin/api/tui_ws.py::_build_initial_snapshot``
emits today (``hosts``, ``credentials``, ``findings``, ``sessions``,
``engagement_id``) plus the broader ``WorkingMemory`` slots that future
slices will surface (objectives, accounts, kill_chain, etc.). Unknown or
missing keys default to empty collections so the panel renderers
gracefully degrade on partial snapshots.

This is the only place where the WS-shape dict and the domain dataclass
meet. Keeping it in ``tui/`` (not ``application/``) preserves the
``no-direct-infra-from-presentation`` layer contract because the adapter
only depends on ``redrobin.domain``.
"""

from __future__ import annotations

from typing import Any

from redrobin.domain.memory import (
    Account,
    Credential,
    Finding,
    Host,
    Objective,
    Session,
    ToolUsage,
    WorkingMemory,
)


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _host_from_dict(d: dict) -> Host:
    return Host(
        name=str(d.get("name", "")),
        os=d.get("os"),
        integrity_level=d.get("integrity_level"),
        edr_detected=d.get("edr_detected"),
        access_level=d.get("access_level"),
    )


def _credential_from_dict(d: dict) -> Credential:
    return Credential(
        domain=str(d.get("domain", "")),
        username=str(d.get("username", "")),
        ntlm_hash=d.get("ntlm_hash"),
        source_beacon_id=d.get("source_beacon_id"),
    )


def _finding_from_dict(d: dict) -> Finding:
    return Finding(
        kind=str(d.get("kind", "")),
        summary=str(d.get("summary", "")),
        source_beacon_id=d.get("source_beacon_id"),
    )


def _session_from_dict(d: dict) -> Session:
    return Session(
        beacon_id=str(d.get("beacon_id", "")),
        hostname=d.get("hostname"),
        user=d.get("user"),
        integrity=d.get("integrity"),
    )


def _account_from_dict(d: dict) -> Account:
    return Account(
        name=str(d.get("name", "")),
        kind=str(d.get("kind", "")),
        source_host=d.get("source_host"),
        captured_at=d.get("captured_at"),
    )


def _objective_from_dict(d: dict) -> Objective:
    return Objective(
        id=str(d.get("id", "")),
        goal=str(d.get("goal", "")),
        status=str(d.get("status", "not_started")),
        priority=str(d.get("priority", "medium")),
        progress_pct=int(d.get("progress_pct", 0) or 0),
        milestones=_as_list(d.get("milestones")),
        pending=_as_list(d.get("pending")),
    )


def _tool_usage_from_dict(d: dict) -> ToolUsage:
    return ToolUsage(
        name=str(d.get("name", "")),
        used_on=str(d.get("used_on", "")),
        used_at=str(d.get("used_at", "")),
        detection_observed=str(d.get("detection_observed", "none")),
    )


def working_memory_from_snapshot(snapshot: dict[str, Any]) -> WorkingMemory:
    """Build a :class:`WorkingMemory` from a WS snapshot dict.

    All collections default to empty so the panel renderers can be called on
    a freshly-connected TUI whose snapshot only contains the daemon's
    ``engagement_id`` placeholder.
    """
    return WorkingMemory(
        engagement_id=str(snapshot.get("engagement_id") or ""),
        client=str(snapshot.get("client") or ""),
        scope_in_scope=_as_list(snapshot.get("scope_in_scope")),
        scope_out_of_scope=_as_list(snapshot.get("scope_out_of_scope")),
        objectives=[
            _objective_from_dict(_as_dict(o)) for o in _as_list(snapshot.get("objectives"))
        ],
        current_phase=str(snapshot.get("current_phase") or "not_started"),
        kill_chain_status=_as_dict(snapshot.get("kill_chain_status")),
        rules_of_engagement=_as_list(snapshot.get("rules_of_engagement")),
        hosts=[_host_from_dict(_as_dict(h)) for h in _as_list(snapshot.get("hosts"))],
        accounts=[_account_from_dict(_as_dict(a)) for a in _as_list(snapshot.get("accounts"))],
        defenses_observed=_as_list(snapshot.get("defenses_observed")),
        tools_used=[
            _tool_usage_from_dict(_as_dict(t)) for t in _as_list(snapshot.get("tools_used"))
        ],
        credentials=[
            _credential_from_dict(_as_dict(c)) for c in _as_list(snapshot.get("credentials"))
        ],
        findings=[_finding_from_dict(_as_dict(f)) for f in _as_list(snapshot.get("findings"))],
        sessions=[_session_from_dict(_as_dict(s)) for s in _as_list(snapshot.get("sessions"))],
    )


__all__ = ["working_memory_from_snapshot"]
