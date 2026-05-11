"""Parametrized validation of the 11 v0.1 event type schemas + their fixtures."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).parents[2] / "protocol" / "schemas" / "v0.1" / "events"
ENVELOPE_SCHEMA = (
    Path(__file__).parents[2] / "protocol" / "schemas" / "v0.1" / "envelope.schema.json"
)
FIXTURES = Path(__file__).parents[2] / "protocol" / "fixtures" / "v0.1"

EVENT_TYPES = [
    "adapter_connected",
    "beacon_initial",
    "beacon_command_input",
    "beacon_command_output",
    "host_observation",
    "credential_observed",
    "opsec_verdict",
    "operator_question",
    "operator_answer",
    "engagement_milestone",
    "adapter_heartbeat",
]


def _load_schema(filename: str) -> dict:
    return json.loads((SCHEMAS / filename).read_text())


@pytest.mark.parametrize("event", EVENT_TYPES)
def test_event_schema_valid_fixture(event):
    schema = _load_schema(f"{event}.schema.json")
    fixture = json.loads((FIXTURES / "valid" / f"{event}.json").read_text())
    # The fixture is the EVENT PAYLOAD (the inner "event" object), not the envelope
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert errors == [], f"{event}: {errors}"


@pytest.mark.parametrize("event", EVENT_TYPES)
def test_event_schema_invalid_fixture(event):
    schema = _load_schema(f"{event}.schema.json")
    fixture = json.loads((FIXTURES / "invalid" / f"{event}.json").read_text())
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert len(errors) > 0, f"{event}: expected invalid, got valid"


def test_event_type_enum_matches_per_event_consts():
    """Cross-validation (I4 from Task 4 review): the envelope's event.type enum
    must exactly equal the union of `const` values from the 11 event schemas.
    Prevents drift between envelope-level enum and per-event-type schemas.
    """
    envelope = json.loads(ENVELOPE_SCHEMA.read_text())
    envelope_enum = set(envelope["properties"]["event"]["properties"]["type"]["enum"])

    per_event_consts = set()
    for event in EVENT_TYPES:
        schema = _load_schema(f"{event}.schema.json")
        const_value = schema["properties"]["type"]["const"]
        per_event_consts.add(const_value)

    assert envelope_enum == per_event_consts, (
        f"Envelope enum drifted from per-event consts.\n"
        f"  in envelope only: {envelope_enum - per_event_consts}\n"
        f"  in events only:   {per_event_consts - envelope_enum}"
    )
