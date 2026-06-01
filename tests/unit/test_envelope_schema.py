import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).parents[2] / "protocol" / "schemas" / "v0.1"
FIXTURES = Path(__file__).parents[2] / "protocol" / "fixtures" / "v0.1"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def _valid_envelope() -> dict:
    return json.loads((FIXTURES / "valid" / "envelope_minimal.json").read_text())


def test_envelope_valid_minimal():
    schema = load_schema("envelope.schema.json")
    fixture = _valid_envelope()
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert errors == [], f"expected valid, got: {errors}"


def test_envelope_rejects_missing_spec_version():
    schema = load_schema("envelope.schema.json")
    fixture = json.loads((FIXTURES / "invalid" / "envelope_no_spec_version.json").read_text())
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert any(e.validator == "required" for e in errors)
    assert any("spec_version" in str(e.message) for e in errors)


@pytest.mark.parametrize(
    "mutation,expected_keyword",
    [
        # spec_version wrong type
        (lambda e: {**e, "spec_version": 1.0}, "type"),
        # spec_version wrong version
        (lambda e: {**e, "spec_version": "0.2.0"}, "pattern"),
        # malformed ts
        (lambda e: {**e, "ts": "this-is-not-a-date"}, "pattern"),
        # extra root property
        (lambda e: {**e, "rogue_field": "x"}, "additionalProperties"),
        # bad ULID — wrong length
        (lambda e: {**e, "id": "evt_TOOSHORT"}, "pattern"),
        # bad ULID — missing prefix
        (lambda e: {**e, "id": "01HZX9KXXR8QF3T2ABCDEFGHJK"}, "pattern"),
        # bad ULID — Crockford-forbidden char
        (lambda e: {**e, "id": "evt_01HZX9KXXR8QF3T2ABCDEFGHIJ"}, "pattern"),
        # unknown event type
        (lambda e: {**e, "event": {"type": "made.up.type"}}, "enum"),
        # missing source.adapter
        (lambda e: {**e, "source": {"version": "1.0.0", "engagement_id": "e1"}}, "required"),
    ],
)
def test_envelope_rejects_invalid_mutations(mutation, expected_keyword):
    schema = load_schema("envelope.schema.json")
    fixture = mutation(_valid_envelope())
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert errors, "expected validation errors for mutation, got none"
    assert any(e.validator == expected_keyword for e in errors), (
        f"expected {expected_keyword!r} validator error; got {[e.validator for e in errors]}"
    )
