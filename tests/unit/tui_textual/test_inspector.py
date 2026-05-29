import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from redrobin.tui.textual_ui import inspector
from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp

SNAP = {
    "engagement_id": "e2e-verify",
    "client": "Acme",
    "current_phase": "recon",
    "scope_in_scope": ["EC2AMAZ-IJVFMFN", "10.100.0.0/24"],
    "scope_out_of_scope": ["10.99.0.0/16"],
    "rules_of_engagement": ["no DoS"],
    "defenses_observed": ["CrowdStrike"],
    "kill_chain_status": {"recon": "done", "access": "in_progress"},
    "objectives": [
        {
            "id": "o1",
            "goal": "Compromise DC",
            "status": "active",
            "progress_pct": 60,
            "priority": "high",
            "milestones": [],
        }
    ],
    "hosts": [
        {
            "name": "EC2AMAZ-IJVFMFN",
            "os": "Win2022",
            "integrity_level": "High",
            "edr_detected": "CrowdStrike",
            "access_level": "SYSTEM",
        }
    ],
    "tools_used": [
        {"name": "mimikatz", "used_on": "EC2AMAZ", "used_at": "t1", "detection_observed": "blocked"}
    ],
    "credentials": [
        {"domain": "CORP", "username": "alice", "ntlm_hash": "aad3b4", "source_beacon_id": "b1"}
    ],
    "findings": [
        {"kind": "kerberos_ticket_observed", "summary": "TGT for alice", "source_beacon_id": "b1"},
        {"kind": "opsec_block", "summary": "mimikatz blocked", "source_beacon_id": "b2"},
    ],
}


def test_sections_match_spec_order():
    keys = [k for k, _ in inspector.SECTIONS]
    assert keys[0] == "engagement"
    assert "findings" in keys
    assert keys[-1] == "findings"
    assert all(title.upper() == title for _, title in inspector.SECTIONS)


def test_section_counts():
    counts = inspector.section_counts(SNAP)
    assert counts["findings"] == 2
    assert counts["credentials"] == 1
    assert counts["hosts"] == 1
    assert counts["objectives"] == 1
    assert counts["engagement"] is None


def test_render_findings_lines():
    lines = inspector.render_section(SNAP, "findings")
    assert any("kerberos_ticket_observed" in line and "TGT for alice" in line for line in lines)
    assert any("b2" in line for line in lines)
    assert len(lines) == 2


def test_render_engagement_lines():
    lines = inspector.render_section(SNAP, "engagement")
    text = " ".join(lines)
    assert "e2e-verify" in text
    assert "Acme" in text
    assert "recon" in text


def test_render_empty_section_is_graceful():
    lines = inspector.render_section({}, "findings")
    assert lines == ["(none)"]


def test_filter_narrows_findings_case_insensitive():
    lines = inspector.render_section(SNAP, "findings", filter_text="KERBEROS")
    assert len(lines) == 1
    assert "kerberos_ticket_observed" in lines[0]


def test_filter_no_match_returns_none_placeholder():
    lines = inspector.render_section(SNAP, "findings", filter_text="zzz-no-such")
    assert lines == ["(none)"]


def test_group_by_kind_adds_subheaders():
    lines = inspector.render_section(SNAP, "findings", group_by="kind")
    assert any(line.startswith("── kerberos_ticket_observed (1)") for line in lines)
    assert any(line.startswith("── opsec_block (1)") for line in lines)
    assert any(line.startswith("  [kerberos_ticket_observed]") for line in lines)


def test_group_by_beacon_groups_by_source():
    lines = inspector.render_section(SNAP, "findings", group_by="beacon")
    assert any(line.startswith("── b1 (1)") for line in lines)
    assert any(line.startswith("── b2 (1)") for line in lines)


class _Host(App):
    def __init__(self, snap):
        super().__init__()
        self._snap = snap

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        yield Static("host")

    def on_mount(self) -> None:
        from redrobin.tui.textual_ui.inspector import InspectorScreen

        self.push_screen(InspectorScreen(self._snap))


@pytest.mark.asyncio
async def test_inspector_opens_with_nav_and_content():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"
        nav = app.screen.query_one("#inspector-nav", OptionList)
        assert nav.option_count == len(inspector.SECTIONS)
        content = app.screen.query_one("#inspector-content", OptionList)
        assert content.option_count >= 1
        assert any(
            "e2e-verify" in str(content.get_option_at_index(i).prompt)
            for i in range(content.option_count)
        )


@pytest.mark.asyncio
async def test_inspector_nav_switches_section():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        findings_idx = [k for k, _ in inspector.SECTIONS].index("findings")
        nav = screen.query_one("#inspector-nav", OptionList)
        nav.highlighted = findings_idx
        await pilot.pause()
        content = screen.query_one("#inspector-content", OptionList)
        prompts = [str(content.get_option_at_index(i).prompt) for i in range(content.option_count)]
        assert any("kerberos_ticket_observed" in p for p in prompts)


@pytest.mark.asyncio
async def test_inspector_escape_dismisses():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "InspectorScreen"


@pytest.mark.asyncio
async def test_inspector_filter_narrows_content():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        nav = screen.query_one("#inspector-nav", OptionList)
        nav.highlighted = [k for k, _ in inspector.SECTIONS].index("findings")
        await pilot.pause()
        screen.query_one("#inspector-filter", Input).value = "kerberos"
        await pilot.pause()
        content = screen.query_one("#inspector-content", OptionList)
        prompts = [str(content.get_option_at_index(i).prompt) for i in range(content.option_count)]
        assert any("kerberos" in p.lower() for p in prompts)
        assert not any("opsec_block" in p for p in prompts)


@pytest.mark.asyncio
async def test_inspector_group_toggle_groups_findings():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        nav = screen.query_one("#inspector-nav", OptionList)
        nav.highlighted = [k for k, _ in inspector.SECTIONS].index("findings")
        await pilot.pause()
        # nav (an OptionList) doesn't consume 'g' → it bubbles to the screen binding
        # (verified: a focused OptionList passes letter keys through to BINDINGS).
        await pilot.press("g")  # None -> kind
        await pilot.pause()
        content = screen.query_one("#inspector-content", OptionList)
        prompts = [str(content.get_option_at_index(i).prompt) for i in range(content.option_count)]
        assert any(p.startswith("── ") for p in prompts)  # a group sub-header appeared


@pytest.mark.asyncio
async def test_app_i_opens_inspector_with_snapshot():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        REGISTRY.dispatch(
            target=app,
            frame={"type": "working_memory_snapshot", "payload": SNAP},
        )
        await pilot.press("i")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"
        content = app.screen.query_one("#inspector-content", OptionList)
        assert any(
            "e2e-verify" in str(content.get_option_at_index(i).prompt)
            for i in range(content.option_count)
        )


@pytest.mark.asyncio
async def test_inspector_copy_row_copies_highlighted(monkeypatch):
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        copied: list[str] = []
        monkeypatch.setattr(app, "copy_to_clipboard", lambda text: copied.append(text))
        content = screen.query_one("#inspector-content", OptionList)
        # Focus first: a focused OptionList resets ``highlighted`` to None on its
        # focus tick (verified, Textual 8.2.7), so set the highlight AFTER focusing
        # (as a real user does — focusing the list, then arrowing to a row).
        content.focus()
        await pilot.pause()
        content.highlighted = 0
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert copied and copied[0] == str(content.get_option_at_index(0).prompt)


# --- Schema-driven (dynamic) record rendering: any secret_type / OS, not AD-only ---


def test_credential_plaintext_shows_secret_and_type():
    s = {
        "credentials": [
            {
                "domain": "",
                "username": "admin",
                "ntlm_hash": None,
                "secret_type": "plaintext",
                "secret": "P@ssw0rd!",
                "source_beacon_id": "997410512",
            }
        ]
    }
    line = inspector._credential_lines(s)[0]
    assert "secret_type=plaintext" in line
    assert "secret=P@ssw0rd!" in line
    assert "src=997410512" in line
    assert "hash=" not in line  # no stale ntlm-only label
    assert not line.startswith("\\")  # empty domain -> no dangling backslash
    assert line.startswith("admin")


def test_credential_ntlm_shows_hash_and_domain():
    s = {
        "credentials": [
            {
                "domain": "CORP",
                "username": "alice",
                "ntlm_hash": "aad3b435:1111",
                "secret_type": "ntlm",
                "secret": None,
                "source_beacon_id": "B-100",
            }
        ]
    }
    line = inspector._credential_lines(s)[0]
    assert line.startswith("CORP\\alice")
    assert "ntlm_hash=aad3b435:1111" in line
    assert "secret_type=ntlm" in line
    assert "secret=" not in line  # null secret dropped


def test_credential_non_ad_secret_type_is_dynamic():
    # proves it is NOT hardcoded to AD/NTLM: an aws_key cred renders fully
    s = {
        "credentials": [
            {
                "domain": "",
                "username": "deploy",
                "secret_type": "aws_key",
                "secret": "AKIAEXAMPLE",
                "source_beacon_id": "b1",
            }
        ]
    }
    line = inspector._credential_lines(s)[0]
    assert "secret_type=aws_key" in line
    assert "secret=AKIAEXAMPLE" in line


def test_credential_unknown_future_field_auto_displays():
    # a field not in today's schema still shows -> schema-driven
    s = {
        "credentials": [
            {
                "username": "svc",
                "secret_type": "kerberos_ticket",
                "ticket_flags": "forwardable,renewable",
                "source_beacon_id": "b1",
            }
        ]
    }
    line = inspector._credential_lines(s)[0]
    assert "ticket_flags=forwardable,renewable" in line


def test_host_shows_plan_a_fields():
    s = {
        "hosts": [
            {
                "name": "JCSP",
                "os": "Microsoft Windows 11 Pro",
                "os_version": "10.0.26200",
                "domain": "WORKGROUP",
                "ip_addresses": ["192.168.130.1", "100.103.167.36"],
                "edr_detected": None,
                "access_level": None,
                "integrity_level": None,
            }
        ]
    }
    line = inspector._host_lines(s)[0]
    assert line.startswith("JCSP")
    assert "os=Microsoft Windows 11 Pro" in line
    assert "os_version=10.0.26200" in line
    assert "domain=WORKGROUP" in line
    assert "192.168.130.1" in line and "100.103.167.36" in line  # list joined
    assert "edr_detected=" not in line  # null fields dropped


def test_host_linux_minimal_no_empty_fields():
    s = {
        "hosts": [
            {
                "name": "web01",
                "os": "Ubuntu 22.04",
                "os_version": None,
                "domain": None,
                "ip_addresses": [],
                "edr_detected": None,
                "access_level": None,
                "integrity_level": None,
            }
        ]
    }
    line = inspector._host_lines(s)[0]
    assert line.startswith("web01")
    assert "os=Ubuntu 22.04" in line
    assert "ip_addresses=" not in line  # empty list dropped
    assert "domain=" not in line


def test_empty_sections_return_empty_lists():
    assert inspector._host_lines({}) == []
    assert inspector._credential_lines({}) == []


def test_false_and_zero_field_values_are_kept():
    # A False/0 field is a real value and must be SHOWN, not dropped by the
    # empty-skip predicate. Locks behavior against a future "skip falsy" refactor.
    s = {"hosts": [{"name": "h1", "edr_detected": False, "open_ports": 0}]}
    line = inspector._host_lines(s)[0]
    assert "edr_detected=False" in line
    assert "open_ports=0" in line


def test_credential_identity_when_no_principal():
    # Both domain and username empty (e.g. a bare token) -> "?" identity, body still renders.
    s = {
        "credentials": [
            {
                "domain": "",
                "username": "",
                "secret_type": "token",
                "secret": "abc123",
                "source_beacon_id": "b1",
            }
        ]
    }
    line = inspector._credential_lines(s)[0]
    assert line.startswith("?")
    assert "secret_type=token" in line
    assert "secret=abc123" in line
