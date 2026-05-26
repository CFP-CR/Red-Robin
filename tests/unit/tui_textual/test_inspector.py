from redrobin.tui.textual_ui import inspector

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
    "accounts": [
        {"name": "CORP\\\\alice", "kind": "ntlm", "source_host": "EC2AMAZ", "captured_at": "t0"}
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
