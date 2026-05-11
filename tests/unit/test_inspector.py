"""Memory inspector overlay rendering."""

from redrobin.domain.memory import Account, Host, Objective, ToolUsage, WorkingMemory
from redrobin.tui.i18n import Labels, load_labels
from redrobin.tui.inspector import render_inspector


def test_inspector_header_includes_engagement_id():
    wm = WorkingMemory(engagement_id="acme-q2-2026")
    out = render_inspector(wm)
    assert "MEMORY INSPECTOR" in out
    assert "acme-q2-2026" in out


def test_inspector_renders_all_sections():
    wm = WorkingMemory(
        engagement_id="acme-q2",
        client="Acme Corp",
        rules_of_engagement=["no destructive", "no DoS"],
        objectives=[Objective(id="obj_1", goal="DC", priority="high", progress_pct=60)],
        kill_chain_status={"recon": "complete", "exfil": "not_started"},
        hosts=[Host(name="DC01", os="Win2022")],
        accounts=[Account(name="svc_sql", kind="plaintext")],
        defenses_observed=["CrowdStrike"],
        tools_used=[ToolUsage(name="nanodump.bof", used_on="DC01", used_at="2026-05-10")],
    )
    out = render_inspector(wm, episodic_lessons=["nanodump invisible to Falcon"])
    assert "Acme Corp" in out
    assert "no destructive" in out
    assert "obj_1" in out
    assert "recon: complete" in out
    assert "DC01" in out
    assert "svc_sql" in out
    assert "CrowdStrike" in out
    assert "nanodump.bof" in out
    assert "nanodump invisible" in out


def test_inspector_handles_empty_engagement():
    wm = WorkingMemory(engagement_id="new")
    out = render_inspector(wm)
    assert "new" in out
    assert "(none defined)" in out  # empty ROE
    assert "(not yet started)" in out  # empty kill chain


def test_inspector_includes_milestones_when_present():
    wm = WorkingMemory(
        engagement_id="x",
        objectives=[
            Objective(
                id="obj_1",
                goal="DC",
                progress_pct=50,
                milestones=["Got initial foothold", "Lateral to DC02"],
            )
        ],
    )
    out = render_inspector(wm)
    assert "Got initial foothold" in out
    assert "Lateral to DC02" in out


def test_inspector_renders_tool_detection():
    wm = WorkingMemory(
        engagement_id="x",
        tools_used=[
            ToolUsage(
                name="mimikatz",
                used_on="DC01",
                used_at="2026-05-10T14:32",
                detection_observed="blocked",
            )
        ],
    )
    out = render_inspector(wm)
    assert "mimikatz" in out
    assert "DC01" in out
    assert "blocked" in out


def test_inspector_uses_provided_labels():
    wm = WorkingMemory(engagement_id="x")
    labels = Labels(load_labels("es"))
    out = render_inspector(wm, labels=labels)
    # Spanish labels should appear (e.g., LECCIONES for lessons)
    assert "LECCIONES" in out


def test_inspector_lessons_count_reflects_input():
    wm = WorkingMemory(engagement_id="x")
    out = render_inspector(wm, episodic_lessons=["a", "b", "c"])
    assert "(3)" in out
    assert "• a" in out
    assert "• b" in out
    assert "• c" in out


def test_inspector_empty_lessons_message():
    wm = WorkingMemory(engagement_id="x")
    out = render_inspector(wm, episodic_lessons=[])
    assert "no lessons yet" in out


def test_inspector_shows_dash_for_missing_client():
    wm = WorkingMemory(engagement_id="x")
    out = render_inspector(wm)
    assert "Client: -" in out
