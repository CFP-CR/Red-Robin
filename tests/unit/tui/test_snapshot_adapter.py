"""Tests for the WS snapshot → domain WorkingMemory adapter."""

from redrobin.tui.snapshot_adapter import working_memory_from_snapshot


def test_empty_snapshot_yields_empty_working_memory():
    wm = working_memory_from_snapshot({"engagement_id": "eng-x"})
    assert wm.engagement_id == "eng-x"
    assert wm.hosts == []
    assert wm.credentials == []
    assert wm.findings == []
    assert wm.sessions == []
    assert wm.accounts == []
    assert wm.objectives == []


def test_missing_engagement_id_defaults_to_empty_string():
    wm = working_memory_from_snapshot({})
    assert wm.engagement_id == ""


def test_hosts_are_rehydrated_as_dataclasses():
    snap = {
        "engagement_id": "eng-x",
        "hosts": [
            {"name": "DC01", "os": "Win2022", "edr_detected": "CrowdStrike"},
            {"name": "WEB01"},
        ],
    }
    wm = working_memory_from_snapshot(snap)
    assert len(wm.hosts) == 2
    assert wm.hosts[0].name == "DC01"
    assert wm.hosts[0].os == "Win2022"
    assert wm.hosts[0].edr_detected == "CrowdStrike"
    assert wm.hosts[1].name == "WEB01"
    assert wm.hosts[1].os is None


def test_credentials_are_rehydrated():
    snap = {
        "engagement_id": "eng-x",
        "credentials": [
            {
                "domain": "CORP",
                "username": "alice",
                "ntlm_hash": "aad3b435b51404eeaad3b435b51404ee:1111",
                "source_beacon_id": "B-1",
            }
        ],
    }
    wm = working_memory_from_snapshot(snap)
    assert len(wm.credentials) == 1
    cred = wm.credentials[0]
    assert cred.domain == "CORP"
    assert cred.username == "alice"
    assert cred.source_beacon_id == "B-1"


def test_findings_are_rehydrated():
    snap = {
        "engagement_id": "eng-x",
        "findings": [
            {"kind": "opsec_block", "summary": "blocked mimikatz", "source_beacon_id": "B-1"}
        ],
    }
    wm = working_memory_from_snapshot(snap)
    assert wm.findings[0].kind == "opsec_block"
    assert wm.findings[0].summary == "blocked mimikatz"


def test_sessions_are_rehydrated():
    snap = {
        "engagement_id": "eng-x",
        "sessions": [
            {
                "beacon_id": "B-1",
                "hostname": "DC01",
                "user": "CORP\\alice",
                "integrity": "high",
            }
        ],
    }
    wm = working_memory_from_snapshot(snap)
    sess = wm.sessions[0]
    assert sess.beacon_id == "B-1"
    assert sess.hostname == "DC01"
    assert sess.user == "CORP\\alice"
    assert sess.integrity == "high"


def test_kill_chain_status_preserves_dict_shape():
    snap = {
        "engagement_id": "eng-x",
        "kill_chain_status": {"recon": "complete", "exfil": "not_started"},
    }
    wm = working_memory_from_snapshot(snap)
    assert wm.kill_chain_status == {"recon": "complete", "exfil": "not_started"}


def test_garbage_values_default_to_empty_collections():
    """Bad types in a snapshot should never crash the adapter."""
    snap = {
        "engagement_id": "eng-x",
        "hosts": "not-a-list",
        "credentials": 42,
        "kill_chain_status": "not-a-dict",
        "objectives": None,
    }
    wm = working_memory_from_snapshot(snap)
    assert wm.hosts == []
    assert wm.credentials == []
    assert wm.kill_chain_status == {}
    assert wm.objectives == []


def test_objectives_are_rehydrated_with_milestones():
    snap = {
        "engagement_id": "eng-x",
        "objectives": [
            {
                "id": "obj_1",
                "goal": "Compromise DC",
                "status": "in_progress",
                "priority": "high",
                "progress_pct": 60,
                "milestones": ["got foothold", "lateral to WEB"],
            }
        ],
    }
    wm = working_memory_from_snapshot(snap)
    obj = wm.objectives[0]
    assert obj.id == "obj_1"
    assert obj.goal == "Compromise DC"
    assert obj.progress_pct == 60
    assert obj.milestones == ["got foothold", "lateral to WEB"]
