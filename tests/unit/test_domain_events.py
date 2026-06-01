"""Domain event dataclasses — wire-protocol-aligned types per spec §4 Componente 7."""

from datetime import datetime, timezone

from ulid import ULID

from redrobin.domain.events import (
    BeaconCommandInputEvent,
    Envelope,
    OpsecVerdictEvent,
    Severity,
    Source,
)


def test_envelope_minimal_construction():
    env = Envelope(
        id=f"evt_{ULID()}",
        ts=datetime.now(timezone.utc),
        source=Source(adapter="cs-aggressor", version="1.0.0", engagement_id="eng_test"),
        event=BeaconCommandInputEvent(
            bid="b1", operator="juan", command="mimikatz", raw="mimikatz logonpasswords"
        ),
    )
    assert env.spec_version == "0.1.0"
    assert env.event.type == "beacon.command_input"


def test_envelope_to_dict_iso_timestamp():
    env = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 10, 14, 32, tzinfo=timezone.utc),
        source=Source(adapter="cs-aggressor", version="1.0.0", engagement_id="eng_test"),
        event=BeaconCommandInputEvent(
            bid="b1", operator="juan", command="mimikatz", raw="mimikatz logonpasswords"
        ),
    )
    d = env.to_dict()
    assert d["ts"] == "2026-05-10T14:32:00+00:00"
    assert d["event"]["type"] == "beacon.command_input"
    assert d["source"]["adapter"] == "cs-aggressor"


def test_envelope_to_dict_with_session_id():
    env = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 10, 14, 32, tzinfo=timezone.utc),
        source=Source(
            adapter="cs-aggressor",
            version="1.0.0",
            engagement_id="eng_test",
            adapter_session_id="ses_01HZX9KMXR1B0K3VABCDEFGHJK",
        ),
        event=BeaconCommandInputEvent(
            bid="b1", operator="juan", command="mimikatz", raw="mimikatz logonpasswords"
        ),
    )
    d = env.to_dict()
    assert d["source"]["adapter_session_id"] == "ses_01HZX9KMXR1B0K3VABCDEFGHJK"


def test_opsec_verdict_severity_enum():
    assert Severity.DANGER.value == "DANGER"
    assert Severity("WARN") == Severity.WARN
    assert Severity("ESCALATE") == Severity.ESCALATE


def test_opsec_verdict_event_serializes_severity_as_string():
    verdict = OpsecVerdictEvent(
        source_event_id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        severity=Severity.DANGER,
        reason="lsass touch",
        rule_id="mimikatz_logonpasswords",
        suggestions=["use nanodump BOF", "or Rubeus /tgtdeleg"],
    )
    env = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 10, 14, 32, tzinfo=timezone.utc),
        source=Source(adapter="core", version="0.1.0", engagement_id="eng_test"),
        event=verdict,
    )
    d = env.to_dict()
    assert d["event"]["severity"] == "DANGER"
    assert d["event"]["type"] == "opsec.verdict"
    assert d["event"]["suggestions"] == ["use nanodump BOF", "or Rubeus /tgtdeleg"]


def test_envelope_to_dict_matches_protocol_schema():
    """Round-trip: envelope.to_dict() must validate against protocol v0.1 envelope schema."""
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).parents[2] / "protocol" / "schemas" / "v0.1" / "envelope.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    validator = Draft202012Validator(schema)

    env = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 10, 14, 32, tzinfo=timezone.utc),
        source=Source(adapter="cs-aggressor", version="1.0.0", engagement_id="eng_test"),
        event=BeaconCommandInputEvent(
            bid="b1", operator="juan", command="mimikatz", raw="mimikatz logonpasswords"
        ),
    )
    d = env.to_dict()
    errors = list(validator.iter_errors(d))
    assert errors == [], f"envelope.to_dict() failed schema validation: {errors}"


def test_command_input_correlation_id_round_trips_and_drops_when_none():
    from redrobin.domain.events import BeaconCommandInputEvent

    with_corr = BeaconCommandInputEvent(
        bid="b1", operator="juan", command="shell", raw="shell systeminfo", correlation_id="18ff"
    )
    assert with_corr.correlation_id == "18ff"

    env = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 27, 14, 32, tzinfo=timezone.utc),
        source=Source(adapter="cs-aggressor", version="1.0.0", engagement_id="eng_test"),
        event=with_corr,
    )
    d = env.to_dict()
    assert d["event"]["correlation_id"] == "18ff"

    # None correlation_id is dropped (schema allows omit; matches task_id behavior)
    no_corr = BeaconCommandInputEvent(
        bid="b1", operator="juan", command="shell", raw="shell systeminfo"
    )
    env2 = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 27, 14, 32, tzinfo=timezone.utc),
        source=Source(adapter="cs-aggressor", version="1.0.0", engagement_id="eng_test"),
        event=no_corr,
    )
    assert "correlation_id" not in env2.to_dict()["event"]


def test_command_input_with_correlation_id_validates_against_schema():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    from redrobin.domain.events import BeaconCommandInputEvent

    schema_path = (
        Path(__file__).parents[2]
        / "protocol"
        / "schemas"
        / "v0.1"
        / "events"
        / "beacon_command_input.schema.json"
    )
    validator = Draft202012Validator(json.loads(schema_path.read_text()))
    event = BeaconCommandInputEvent(
        bid="b1", operator="juan", command="shell", raw="shell systeminfo", correlation_id="18ff"
    )
    payload = {
        "type": event.type,
        "bid": event.bid,
        "operator": event.operator,
        "command": event.command,
        "raw": event.raw,
        "correlation_id": event.correlation_id,
    }
    assert list(validator.iter_errors(payload)) == []


def test_command_output_with_correlation_id_validates_against_schema():
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    from redrobin.domain.events import BeaconCommandOutputEvent

    schema_path = (
        Path(__file__).parents[2]
        / "protocol"
        / "schemas"
        / "v0.1"
        / "events"
        / "beacon_command_output.schema.json"
    )
    validator = Draft202012Validator(json.loads(schema_path.read_text()))
    event = BeaconCommandOutputEvent(
        bid="b1", task_id="30", output="Host Name: WS01", correlation_id="18ff"
    )
    payload = {
        "type": event.type,
        "bid": event.bid,
        "task_id": event.task_id,
        "output": event.output,
        "correlation_id": event.correlation_id,
    }
    assert list(validator.iter_errors(payload)) == []


def test_command_output_correlation_id_dropped_when_none():
    from redrobin.domain.events import BeaconCommandOutputEvent

    env = Envelope(
        id="evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        ts=datetime(2026, 5, 27, 14, 32, tzinfo=timezone.utc),
        source=Source(adapter="cs-aggressor", version="1.0.0", engagement_id="eng_test"),
        event=BeaconCommandOutputEvent(bid="b1", task_id="30", output="x"),
    )
    d = env.to_dict()
    assert "correlation_id" not in d["event"]
    assert "exit_status" not in d["event"]  # existing drop still holds
