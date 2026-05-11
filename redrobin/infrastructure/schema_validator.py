"""redrobin/infrastructure/schema_validator.py — compile-once jsonschema validators.

Per spec §13.3: Draft 2020-12 validators compiled at module import time and reused
for every event. Far cheaper than re-compiling per-event.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "protocol" / "schemas" / "v0.1"


def _load_validators() -> tuple[Draft202012Validator, dict[str, Draft202012Validator]]:
    envelope_schema = json.loads((PROTOCOL_DIR / "envelope.schema.json").read_text())
    envelope_validator = Draft202012Validator(envelope_schema)

    event_validators: dict[str, Draft202012Validator] = {}
    for path in (PROTOCOL_DIR / "events").glob("*.schema.json"):
        schema = json.loads(path.read_text())
        const_type = schema.get("properties", {}).get("type", {}).get("const")
        if const_type:
            event_validators[const_type] = Draft202012Validator(schema)
    return envelope_validator, event_validators


# Module-level globals — compiled once at import time
ENVELOPE_VALIDATOR, EVENT_VALIDATORS = _load_validators()


def validate_envelope(data: dict) -> list[str]:
    """Validate envelope structure. Returns list of error messages (empty = valid)."""
    return [e.message for e in ENVELOPE_VALIDATOR.iter_errors(data)]


def validate_event(event_type: str, payload: dict) -> list[str]:
    """Validate event payload. Returns list of error messages (empty = valid)."""
    validator = EVENT_VALIDATORS.get(event_type)
    if validator is None:
        return [f"no schema registered for event type {event_type!r}"]
    return [e.message for e in validator.iter_errors(payload)]
