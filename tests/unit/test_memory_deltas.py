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
            # carries secret material so the #114 drop rule keeps it; secret_type
            # stays "" to exercise the empty-string -> "ntlm" fallback.
            "credentials": [{"username": "u", "secret_type": "", "secret": "deadbeef"}],
        },
        source_beacon_id=None,
    )
    assert deltas.tools_used[0].name == "nanodump"
    assert deltas.tools_used[0].used_on == "WS01"
    assert deltas.tools_used[0].used_at  # auto-stamped ISO timestamp, non-empty
    assert deltas.credentials[0].secret_type == "ntlm"  # empty string -> default


def test_from_payload_drops_credential_with_no_secret() -> None:
    # #114: a username with no secret material is a username observation, not a
    # credential. The enricher sometimes emits the logged-in user this way.
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"domain": "CORP", "username": "jdoe"}]},
        source_beacon_id="B-1",
    )
    assert deltas.credentials == []


def test_from_payload_keeps_credential_with_plaintext_secret() -> None:
    deltas = WorkingMemoryDeltas.from_payload(
        {
            "credentials": [
                {"username": "svc_sql", "secret": "Summer2026!", "secret_type": "plaintext"}
            ]
        },
        source_beacon_id="B-1",
    )
    assert len(deltas.credentials) == 1
    assert deltas.credentials[0].username == "svc_sql"
    assert deltas.credentials[0].secret == "Summer2026!"


def test_from_payload_keeps_credential_with_ntlm_hash() -> None:
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"username": "admin", "ntlm_hash": "aad3b435b51404ee:31d6cfe0d16ae931"}]},
        source_beacon_id="B-1",
    )
    assert len(deltas.credentials) == 1
    assert deltas.credentials[0].ntlm_hash == "aad3b435b51404ee:31d6cfe0d16ae931"


def test_from_payload_drops_credential_with_empty_secret_strings() -> None:
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"username": "jdoe", "secret": "", "ntlm_hash": ""}]},
        source_beacon_id="B-1",
    )
    assert deltas.credentials == []
