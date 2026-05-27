"""Unit tests for the WorkingMemoryDeltas contract (whitelisting + provenance)."""

from __future__ import annotations

from redrobin.domain.memory import WorkingMemoryDeltas


def test_from_payload_builds_typed_objects_and_attaches_provenance():
    deltas = WorkingMemoryDeltas.from_payload(
        {
            "hosts": [
                {
                    "name": "ws01",
                    "os": "Windows 10",
                    "os_version": "10.0.19045",
                    "domain": "ACME",
                    "ip_addresses": ["10.0.0.5"],
                }
            ],
            "credentials": [
                {"domain": "ACME", "username": "jdoe", "secret_type": "plaintext", "secret": "pw"}
            ],
            "findings": [{"kind": "gpp_password", "summary": "Groups.xml cpassword"}],
            "defenses_observed": ["Defender"],
        },
        source_beacon_id="B-7",
    )
    assert deltas.hosts[0].domain == "ACME"
    assert deltas.hosts[0].ip_addresses == ["10.0.0.5"]
    # Provenance is system-supplied, never from the LLM payload.
    assert deltas.credentials[0].source_beacon_id == "B-7"
    assert deltas.findings[0].source_beacon_id == "B-7"
    assert deltas.defenses_observed == ["Defender"]


def test_from_payload_ignores_unknown_keys_and_bad_rows():
    deltas = WorkingMemoryDeltas.from_payload(
        {
            "hosts": [
                {"name": "ws01", "evil_field": "DROP TABLE", "os": "Win"},  # extra key ignored
                {"os": "no name"},  # no name -> skipped
            ],
            "credentials": [{"domain": "ACME"}],  # no username -> skipped
            "junk_top_level_key": 123,  # ignored
        },
        source_beacon_id=None,
    )
    assert len(deltas.hosts) == 1
    assert deltas.hosts[0].name == "ws01"
    assert not hasattr(deltas.hosts[0], "evil_field")
    assert deltas.credentials == []


def test_from_payload_handles_non_dict_and_empty():
    assert WorkingMemoryDeltas.from_payload(None, source_beacon_id=None).hosts == []
    assert WorkingMemoryDeltas.from_payload({}, source_beacon_id=None).credentials == []


def test_round_trip_to_payload_from_payload_is_stable():
    src = WorkingMemoryDeltas.from_payload(
        {"hosts": [{"name": "WS01", "domain": "ACME", "ip_addresses": ["10.0.0.5"]}]},
        source_beacon_id="B-1",
    )
    again = WorkingMemoryDeltas.from_payload(src.to_payload(), source_beacon_id="B-1")
    assert again.hosts[0].name == "WS01"
    assert again.hosts[0].ip_addresses == ["10.0.0.5"]


def test_from_payload_tools_used_and_secret_type_fallback():
    """tools_used builds with an auto-stamped used_at; empty secret_type falls back to ntlm."""
    deltas = WorkingMemoryDeltas.from_payload(
        {
            "tools_used": [{"name": "nanodump", "used_on": "WS01"}],
            "credentials": [{"username": "u", "secret_type": ""}],
        },
        source_beacon_id=None,
    )
    assert deltas.tools_used[0].name == "nanodump"
    assert deltas.tools_used[0].used_on == "WS01"
    assert deltas.tools_used[0].used_at  # auto-stamped ISO timestamp, non-empty
    assert deltas.credentials[0].secret_type == "ntlm"  # empty string -> default
