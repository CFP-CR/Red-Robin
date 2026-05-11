"""protocol/validator.py — CLI to validate wire protocol envelopes against v0.1 schemas.

Compiles validators ONCE at module-import time (per spec §13.3 jsonschema performance).
Subsequent invocations reuse the compiled validators.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).parent / "schemas" / "v0.1"


def _load_validators() -> tuple[Draft202012Validator, dict[str, Draft202012Validator]]:
    """Load envelope + per-event-type validators at module-import time.

    Returns (envelope_validator, {event_type: event_validator}).
    """
    envelope_schema = json.loads((SCHEMA_DIR / "envelope.schema.json").read_text())
    envelope_validator = Draft202012Validator(envelope_schema)

    event_validators: dict[str, Draft202012Validator] = {}
    for path in (SCHEMA_DIR / "events").glob("*.schema.json"):
        schema = json.loads(path.read_text())
        const_type = schema.get("properties", {}).get("type", {}).get("const")
        if const_type:
            event_validators[const_type] = Draft202012Validator(schema)
    return envelope_validator, event_validators


_ENVELOPE_VALIDATOR, _EVENT_VALIDATORS = _load_validators()


def validate_file(path: Path) -> int:
    """Validate one envelope JSON file. Return 0 on success, non-zero on failure."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"JSON PARSE ERROR: {e}", file=sys.stderr)
        return 2

    envelope_errors = list(_ENVELOPE_VALIDATOR.iter_errors(data))
    if envelope_errors:
        for e in envelope_errors:
            print(f"ENVELOPE ERROR: {e.message}", file=sys.stderr)
        return 1

    event_type = data["event"]["type"]
    event_validator = _EVENT_VALIDATORS.get(event_type)
    if event_validator is None:
        print(f"EVENT ERROR: no schema registered for event type {event_type!r}", file=sys.stderr)
        return 1

    event_errors = list(event_validator.iter_errors(data["event"]))
    if event_errors:
        for e in event_errors:
            print(f"EVENT ERROR: {e.message}", file=sys.stderr)
        return 1

    print(f"VALID: {path}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python protocol/validator.py <envelope.json>", file=sys.stderr)
        return 2
    return validate_file(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
