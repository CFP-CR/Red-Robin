"""TUI panel rendering tests."""

from redrobin.domain.memory import Account, Host, Objective, ToolUsage, WorkingMemory
from redrobin.tui.i18n import Labels, load_labels
from redrobin.tui.panels.chat import ChatTurn, render_chat
from redrobin.tui.panels.event_stream import EventEntry, render_event_stream
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


def test_render_event_stream_empty():
    assert render_event_stream([]) == "(no events yet)"


def test_render_event_stream_formats_with_severity():
    entries = [
        EventEntry(timestamp="2026-05-10T14:32:01Z", severity="DANGER", text="mimikatz blocked"),
        EventEntry(timestamp="2026-05-10T14:32:15Z", severity="WARN", text="AD recon"),
    ]
    out = render_event_stream(entries)
    assert "[14:32:01]" in out
    assert "[DANGER]" in out
    assert "mimikatz blocked" in out


def test_render_event_stream_handles_non_iso_timestamp():
    entry = EventEntry(timestamp="14:32:01", severity="INFO", text="hi")
    out = render_event_stream([entry])
    assert "[14:32:01]" in out
    assert "[INFO" in out


def test_render_chat_empty():
    assert "empty" in render_chat([])


def test_render_chat_with_turns():
    turns = [
        ChatTurn(role="operator", text="What's safe for cred dump?"),
        ChatTurn(role="red-robin", text="Use nanodump", citations=["vault/nanodump.md"]),
    ]
    out = render_chat(turns)
    assert "What's safe" in out
    assert "nanodump" in out
    assert "vault/nanodump.md" in out


def test_render_chat_operator_prefix():
    out = render_chat([ChatTurn(role="operator", text="hello")])
    assert out.startswith("> hello")


def test_render_chat_red_robin_prefix():
    out = render_chat([ChatTurn(role="red-robin", text="hi")])
    # Should use the bird emoji prefix
    assert "hi" in out
    assert not out.startswith("> ")
