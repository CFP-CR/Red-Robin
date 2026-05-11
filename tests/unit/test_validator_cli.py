"""CLI validator for v0.1 envelopes."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
VALIDATOR = REPO_ROOT / "protocol" / "validator.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_validator_accepts_valid_envelope():
    fixture = REPO_ROOT / "protocol" / "fixtures" / "v0.1" / "valid" / "envelope_minimal.json"
    result = _run([str(fixture)])
    assert result.returncode == 0, result.stderr
    assert "VALID" in result.stdout


def test_validator_rejects_envelope_missing_spec_version():
    fixture = (
        REPO_ROOT / "protocol" / "fixtures" / "v0.1" / "invalid" / "envelope_no_spec_version.json"
    )
    result = _run([str(fixture)])
    assert result.returncode != 0
    assert "spec_version" in result.stderr


def test_validator_rejects_envelope_with_invalid_event_payload(tmp_path):
    # Construct an envelope whose `event` payload violates its event-type schema
    envelope = {
        "spec_version": "0.1.0",
        "id": "evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        "ts": "2026-05-10T14:32:00.000Z",
        "source": {
            "adapter": "cs-aggressor",
            "version": "1.0.0",
            "engagement_id": "eng_acme_q2",
        },
        "event": {
            "type": "beacon.command_input",
            # missing required "bid", "operator", "command", "raw"
        },
    }
    path = tmp_path / "bad_event.json"
    path.write_text(json.dumps(envelope))
    result = _run([str(path)])
    assert result.returncode != 0
    assert "EVENT ERROR" in result.stderr


def test_validator_rejects_unknown_event_type(tmp_path):
    envelope = {
        "spec_version": "0.1.0",
        "id": "evt_01HZX9KXXR8QF3T2ABCDEFGHJK",
        "ts": "2026-05-10T14:32:00.000Z",
        "source": {"adapter": "x", "version": "1.0.0", "engagement_id": "e1"},
        "event": {"type": "made.up.type"},
    }
    path = tmp_path / "bad_type.json"
    path.write_text(json.dumps(envelope))
    result = _run([str(path)])
    # The envelope schema's enum rejects this at envelope-validation stage,
    # so the failure is ENVELOPE ERROR (enum violation), not EVENT ERROR.
    assert result.returncode != 0
    # Could be either ENVELOPE ERROR (enum) or EVENT ERROR (no schema) — accept either.
    assert "ERROR" in result.stderr
