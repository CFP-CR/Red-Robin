"""Inspector ModalScreen + pure section helpers for the Textual TUI (Phase 4).

The helpers turn the app's working-memory *snapshot dict* (from
``working_memory_to_snapshot`` = ``asdict(WorkingMemory)``) into per-section text
lines — porting the formatting from the legacy ``redrobin/tui/inspector.py`` but
dict-based and split into navigable sections instead of one overwhelming string.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

# Ordered (key, display title). Mirrors spec §3.3, plus KILL CHAIN (data is in the
# snapshot and the legacy inspector showed it — kept for parity).
SECTIONS: list[tuple[str, str]] = [
    ("engagement", "ENGAGEMENT"),
    ("scope", "SCOPE"),
    ("roe", "ROE"),
    ("objectives", "OBJECTIVES"),
    ("kill_chain", "KILL CHAIN"),
    ("hosts", "HOSTS"),
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


def _fmt_value(v: Any) -> str:
    """List/tuple -> comma-joined non-empty items; scalars -> str()."""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if x is not None and x != "")
    return str(v)


def _kv_body(item: dict[str, Any], *, skip: set[str]) -> str:
    """Every POPULATED field (except ``skip``) as 'key=value', in field order.

    Schema-driven: renders whatever keys are present — any secret_type, any OS,
    fields added later — nothing hardcoded. None / "" / [] / {} are dropped.
    """
    parts: list[str] = []
    for k, v in item.items():
        if k in skip or v is None or v == "" or v == [] or v == {}:
            continue
        rendered = _fmt_value(v)
        if rendered == "":
            continue
        parts.append(f"{k}={rendered}")
    return " ".join(parts)


def _named_lines(records: list[dict[str, Any]]) -> list[str]:
    """Identity = ``name``, then every other populated field (host/tool)."""
    out: list[str] = []
    for r in records:
        ident = r.get("name") or "?"
        body = _kv_body(r, skip={"name"})
        out.append(f"{ident} {body}".rstrip())
    return out


def _host_lines(s: dict[str, Any]) -> list[str]:
    return _named_lines(s.get("hosts") or [])


def _tool_lines(s: dict[str, Any]) -> list[str]:
    return _named_lines(s.get("tools_used") or [])


def _credential_lines(s: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for c in s.get("credentials") or []:
        dom = c.get("domain") or ""
        user = c.get("username") or ""
        ident = f"{dom}\\{user}" if dom else (user or "?")
        body = _kv_body(c, skip={"domain", "username", "source_beacon_id"})
        src = c.get("source_beacon_id") or "-"
        out.append(" ".join(p for p in (ident, body, f"src={src}") if p))
    return out


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


class InspectorScreen(ModalScreen):
    """Full-engagement-state inspector. ``i`` opens it (from the app), ``Esc`` closes.
    Left nav picks a section; the content list shows that section's lines."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("g", "cycle_group", "Group"),
        ("y", "copy_row", "Copy"),
    ]

    def __init__(self, snapshot: dict[str, Any]) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._section = SECTIONS[0][0]
        self._group_by: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="inspector"):
            yield Static(
                f"Memory Inspector — {self._snapshot.get('engagement_id') or '-'}",
                id="inspector-title",
            )
            with Horizontal(id="inspector-main"):
                yield OptionList(id="inspector-nav")
                with Vertical(id="inspector-body"):
                    yield Input(placeholder="filter…", id="inspector-filter")
                    yield OptionList(id="inspector-content")
            yield Static(
                "Esc close · ↑↓ navigate · g group findings · y copy row", id="inspector-hint"
            )

    def on_mount(self) -> None:
        nav = self.query_one("#inspector-nav", OptionList)
        counts = section_counts(self._snapshot)
        for key, title in SECTIONS:
            count = counts.get(key)
            label = f"{title} ({count})" if count is not None else title
            nav.add_option(Option(Text(label), id=key))
        nav.highlighted = 0
        nav.focus()
        self._render_content()

    def _render_content(self) -> None:
        content = self.query_one("#inspector-content", OptionList)
        content.clear_options()
        filter_text = self.query_one("#inspector-filter", Input).value
        lines = render_section(
            self._snapshot, self._section, filter_text=filter_text, group_by=self._group_by
        )
        content.add_options([Option(Text(line)) for line in lines])

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        # Only the nav switches sections; the content list's own highlight is for copy-row.
        if event.option_list.id == "inspector-nav" and event.option_id:
            self._section = event.option_id
            self._render_content()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._render_content()

    def action_cycle_group(self) -> None:
        order: list[str | None] = [None, "kind", "beacon"]
        self._group_by = order[(order.index(self._group_by) + 1) % len(order)]
        self._render_content()

    def action_copy_row(self) -> None:
        content = self.query_one("#inspector-content", OptionList)
        index = content.highlighted
        if index is not None:
            text = str(content.get_option_at_index(index).prompt)
            self.app.copy_to_clipboard(text)
            self.app.notify("Copied row to clipboard")

    def action_close(self) -> None:
        self.dismiss()
