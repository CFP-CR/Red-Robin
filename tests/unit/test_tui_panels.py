"""TUI panel rendering tests.

Currently only ``panels/working_memory.py`` is integrated into the live TUI
(``redrobin/tui/app.py``). The earlier ``panels/chat.py`` and
``panels/event_stream.py`` modules were removed in Phase 2 condition 3 —
they expected typed inputs the live WS stream does not produce, and the
inline ``_render_events`` in ``app.py`` covers the live verdict stream.
"""

from redrobin.domain.memory import Account, Host, Objective, ToolUsage, WorkingMemory
from redrobin.tui.i18n import Labels, load_labels
from redrobin.tui.panels.working_memory import render_working_memory


def _labels(locale: str = "en") -> Labels:
    return Labels(load_labels(locale))


def test_render_working_memory_includes_engagement_and_phase():
    wm = WorkingMemory(engagement_id="acme-q2", current_phase="credential_access")
    out = render_working_memory(wm, _labels())
    assert "acme-q2" in out
    assert "credential_access" in out


def test_render_working_memory_objectives_progress_bar():
    wm = WorkingMemory(
        engagement_id="x",
        objectives=[Objective(id="obj_1", goal="DC", progress_pct=60, status="in_progress")],
    )
    out = render_working_memory(wm, _labels())
    assert "obj_1" in out
    assert "60%" in out
    assert "in_progress" in out


def test_render_working_memory_hosts_section():
    wm = WorkingMemory(
        engagement_id="x",
        hosts=[Host(name="DC01", os="Win2022", edr_detected="CrowdStrike", access_level="SYSTEM")],
    )
    out = render_working_memory(wm, _labels())
    assert "DC01" in out
    assert "CrowdStrike" in out
    assert "SYSTEM" in out


def test_render_working_memory_tools_used_section():
    wm = WorkingMemory(
        engagement_id="x",
        tools_used=[ToolUsage(name="nanodump.bof", used_on="DC01", used_at="2026-05-10T14:48:00Z")],
    )
    out = render_working_memory(wm, _labels())
    assert "nanodump.bof" in out
    assert "DC01" in out


def test_render_working_memory_accounts_section():
    wm = WorkingMemory(
        engagement_id="x",
        accounts=[Account(name="acme\\svc_sql", kind="plaintext", source_host="DC01")],
    )
    out = render_working_memory(wm, _labels())
    assert "acme\\svc_sql" in out
    assert "plaintext" in out
    assert "DC01" in out


def test_render_working_memory_defenses_section():
    wm = WorkingMemory(
        engagement_id="x",
        defenses_observed=["CrowdStrike Falcon on DC01"],
    )
    out = render_working_memory(wm, _labels())
    assert "CrowdStrike Falcon on DC01" in out


def test_render_working_memory_kill_chain_markers():
    wm = WorkingMemory(
        engagement_id="x",
        kill_chain_status={"recon": "complete", "creds": "in_progress", "exfil": "not_started"},
    )
    out = render_working_memory(wm, _labels())
    assert "recon✓" in out
    assert "creds⚙" in out
    assert "exfil—" in out


def test_render_working_memory_uses_spanish_labels():
    wm = WorkingMemory(engagement_id="x")
    out = render_working_memory(wm, _labels("es"))
    assert "FASE" in out


def test_render_sessions_uses_session_field_names():
    """#143: a serialized Session has keys beacon_id/hostname/user/integrity,
    NOT id/name/host — the panel must read the real keys or it shows '?'."""
    from redrobin.tui.app import _render_sessions
    from redrobin.tui.state import TUIState

    state = TUIState()
    state.snapshot["sessions"] = [
        {"beacon_id": "B-42", "hostname": "DC01", "user": "CORP\\admin", "integrity": "High"}
    ]
    out = _render_sessions(state)
    assert "B-42" in out
    assert "DC01" in out
    assert "?" not in out


def test_render_sessions_empty():
    from redrobin.tui.app import _render_sessions
    from redrobin.tui.state import TUIState

    assert _render_sessions(TUIState()) == "(no active sessions)"
