import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMAS = Path("protocol/schemas/v0.1")
FIXTURES = Path("protocol/fixtures/v0.1")


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def test_envelope_valid_minimal():
    schema = load_schema("envelope.schema.json")
    fixture = json.loads((FIXTURES / "valid" / "envelope_minimal.json").read_text())
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert errors == [], f"expected valid, got: {errors}"


def test_envelope_rejects_missing_spec_version():
    schema = load_schema("envelope.schema.json")
    fixture = json.loads((FIXTURES / "invalid" / "envelope_no_spec_version.json").read_text())
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(fixture))
    assert any("spec_version" in str(e.message) for e in errors), (
        f"expected spec_version error, got: {errors}"
    )
