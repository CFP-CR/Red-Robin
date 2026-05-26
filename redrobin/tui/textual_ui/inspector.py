"""Inspector ModalScreen + pure section helpers for the Textual TUI (Phase 4).

The helpers turn the app's working-memory *snapshot dict* (from
``working_memory_to_snapshot`` = ``asdict(WorkingMemory)``) into per-section text
lines — porting the formatting from the legacy ``redrobin/tui/inspector.py`` but
dict-based and split into navigable sections instead of one overwhelming string.
"""

from __future__ import annotations

from typing import Any

# Ordered (key, display title). Mirrors spec §3.3, plus KILL CHAIN (data is in the
# snapshot and the legacy inspector showed it — kept for parity).
SECTIONS: list[tuple[str, str]] = [
    ("engagement", "ENGAGEMENT"),
    ("scope", "SCOPE"),
    ("roe", "ROE"),
    ("objectives", "OBJECTIVES"),
    ("kill_chain", "KILL CHAIN"),
    ("hosts", "HOSTS"),
    ("accounts", "ACCOUNTS"),
    ("defenses", "DEFENSES"),
    ("tools", "TOOLS"),
    ("credentials", "CREDENTIALS"),
    ("findings", "FINDINGS"),
]


def section_counts(snapshot: dict[str, Any]) -> dict[str, int | None]:
    """Item count per section for the nav labels. ``engagement`` is singular → None."""
    return {
        "engagement": None,
        "scope": len(snapshot.get("scope_in_scope") or [])
        + len(snapshot.get("scope_out_of_scope") or []),
        "roe": len(snapshot.get("rules_of_engagement") or []),
        "objectives": len(snapshot.get("objectives") or []),
        "kill_chain": len(snapshot.get("kill_chain_status") or {}),
        "hosts": len(snapshot.get("hosts") or []),
        "accounts": len(snapshot.get("accounts") or []),
        "defenses": len(snapshot.get("defenses_observed") or []),
        "tools": len(snapshot.get("tools_used") or []),
        "credentials": len(snapshot.get("credentials") or []),
        "findings": len(snapshot.get("findings") or []),
    }


def _engagement_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"ID:     {s.get('engagement_id') or '-'}",
        f"Client: {s.get('client') or '-'}",
        f"Phase:  {s.get('current_phase') or '-'}",
    ]


def _scope_lines(s: dict[str, Any]) -> list[str]:
    out = [f"in:  {x}" for x in (s.get("scope_in_scope") or [])]
    out += [f"out: {x}" for x in (s.get("scope_out_of_scope") or [])]
    return out


def _objective_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{o.get('id', '?')}: {o.get('goal', '')} "
        f"[{o.get('status', '?')}, {o.get('progress_pct', 0)}%, prio={o.get('priority', '?')}]"
        for o in (s.get("objectives") or [])
    ]


def _kill_chain_lines(s: dict[str, Any]) -> list[str]:
    return [f"{phase}: {status}" for phase, status in (s.get("kill_chain_status") or {}).items()]


def _host_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{h.get('name', '?')} [{h.get('os') or '-'}] EDR={h.get('edr_detected') or '-'} "
        f"access={h.get('access_level') or '-'} integrity={h.get('integrity_level') or '-'}"
        for h in (s.get("hosts") or [])
    ]


def _account_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{a.get('name', '?')} [{a.get('kind', '?')}] source={a.get('source_host') or '-'} "
        f"captured={a.get('captured_at') or '-'}"
        for a in (s.get("accounts") or [])
    ]


def _tool_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{t.get('name', '?')} on {t.get('used_on') or '-'} at {t.get('used_at') or '-'} "
        f"— {t.get('detection_observed') or '-'}"
        for t in (s.get("tools_used") or [])
    ]


def _credential_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{c.get('domain', '?')}\\{c.get('username', '?')} "
        f"hash={c.get('ntlm_hash') or '-'} src={c.get('source_beacon_id') or '-'}"
        for c in (s.get("credentials") or [])
    ]


def _finding_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"[{f.get('kind', '?')}] {f.get('summary', '')} (src={f.get('source_beacon_id') or '-'})"
        for f in (s.get("findings") or [])
    ]


def _str_lines(s: dict[str, Any], key: str) -> list[str]:
    return [f"- {x}" for x in (s.get(key) or [])]


def render_section(
    snapshot: dict[str, Any],
    section: str,
    *,
    filter_text: str = "",
    group_by: str | None = None,
) -> list[str]:
    """Text lines for ``section``. ``filter_text`` keeps case-insensitive substring
    matches; ``group_by`` ('kind'|'beacon') groups FINDINGS under sub-headers."""
    if section == "engagement":
        lines = _engagement_lines(snapshot)
    elif section == "scope":
        lines = _scope_lines(snapshot)
    elif section == "roe":
        lines = _str_lines(snapshot, "rules_of_engagement")
    elif section == "objectives":
        lines = _objective_lines(snapshot)
    elif section == "kill_chain":
        lines = _kill_chain_lines(snapshot)
    elif section == "hosts":
        lines = _host_lines(snapshot)
    elif section == "accounts":
        lines = _account_lines(snapshot)
    elif section == "defenses":
        lines = _str_lines(snapshot, "defenses_observed")
    elif section == "tools":
        lines = _tool_lines(snapshot)
    elif section == "credentials":
        lines = _credential_lines(snapshot)
    elif section == "findings":
        lines = _grouped_findings(snapshot, group_by) if group_by else _finding_lines(snapshot)
    else:
        lines = []

    if filter_text:
        needle = filter_text.lower()
        lines = [ln for ln in lines if needle in ln.lower()]
    return lines or ["(none)"]


def _grouped_findings(snapshot: dict[str, Any], group_by: str) -> list[str]:
    """Findings grouped under '── <group> (<n>) ──' sub-headers."""
    key = "kind" if group_by == "kind" else "source_beacon_id"
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in snapshot.get("findings") or []:
        groups.setdefault(str(f.get(key) or "-"), []).append(f)
    out: list[str] = []
    for group_value, items in groups.items():
        out.append(f"── {group_value} ({len(items)}) ──")
        out += [
            f"  [{f.get('kind', '?')}] {f.get('summary', '')} (src={f.get('source_beacon_id') or '-'})"
            for f in items
        ]
    return out
