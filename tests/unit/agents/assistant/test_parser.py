from redrobin.application.agents.assistant.parser import parse_recommendation
from redrobin.domain.events import Severity


def test_parses_full_recommendation():
    raw = (
        '{"suggested_action":"Validate svc_reports against SQL01",'
        '"rationale":"CrowdStrike observed; a bind is quiet vs a dump",'
        '"opsec_risk":"WARN","confidence":"High cred is valid; unsure scope",'
        '"references":["vault/mssql.md#chunk_0"]}'
    )
    rec = parse_recommendation(raw, correlation_id="18ff")
    assert rec is not None
    assert rec.suggested_action.startswith("Validate")
    assert rec.opsec_risk is Severity.WARN
    assert rec.correlation_id == "18ff"
    assert rec.references == ["vault/mssql.md#chunk_0"]


def test_strips_markdown_fences():
    raw = '```json\n{"suggested_action":"a","rationale":"b"}\n```'
    rec = parse_recommendation(raw, correlation_id=None)
    assert rec is not None and rec.suggested_action == "a"


def test_stay_silent_sentinel_returns_none():
    assert parse_recommendation('{"speak": false}', correlation_id="x") is None


def test_missing_action_or_rationale_returns_none():
    assert parse_recommendation('{"suggested_action":"a"}', correlation_id="x") is None
    assert parse_recommendation('{"rationale":"b"}', correlation_id="x") is None


def test_empty_and_malformed_return_none():
    assert parse_recommendation("", correlation_id="x") is None
    assert parse_recommendation("not json at all", correlation_id="x") is None
    assert parse_recommendation("[1, 2, 3]", correlation_id="x") is None  # not an object


def test_unknown_severity_falls_back_to_info():
    rec = parse_recommendation(
        '{"suggested_action":"a","rationale":"b","opsec_risk":"NOPE"}', correlation_id=None
    )
    assert rec is not None and rec.opsec_risk is Severity.INFO
