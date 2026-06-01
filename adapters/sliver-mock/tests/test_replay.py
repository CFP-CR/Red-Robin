"""Sliver-Mock adapter — scenario replay tests (HTTP layer mocked)."""

import json
import sys
from pathlib import Path


ADAPTER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADAPTER_ROOT))


def test_linux_demo_scenario_parses():
    scenario = json.loads((ADAPTER_ROOT / "scenarios" / "linux_demo.json").read_text())
    assert isinstance(scenario, list)
    assert len(scenario) >= 3
    for entry in scenario:
        assert "delay_ms" in entry
        assert "envelope" in entry
        env = entry["envelope"]
        assert env["spec_version"] == "0.1.0"
        assert env["source"]["adapter"] == "sliver-mock"


def test_acme_corp_scenario_parses():
    scenario = json.loads((ADAPTER_ROOT / "scenarios" / "acme_corp.json").read_text())
    assert isinstance(scenario, list)
    assert len(scenario) >= 4


def test_all_scenarios_validate_against_envelope_schema():
    """Every event in every scenario must validate against the v0.1 envelope schema."""
    import json as _json
    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[3]
        / "protocol"
        / "schemas"
        / "v0.1"
        / "envelope.schema.json"
    )
    schema = _json.loads(schema_path.read_text())
    validator = Draft202012Validator(schema)

    for scenario_path in (ADAPTER_ROOT / "scenarios").glob("*.json"):
        scenario = json.loads(scenario_path.read_text())
        for i, entry in enumerate(scenario):
            errors = list(validator.iter_errors(entry["envelope"]))
            assert errors == [], f"{scenario_path.name}[{i}] failed envelope schema: {errors}"
