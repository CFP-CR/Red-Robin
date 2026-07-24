from redrobin.domain.assistant import AgentTool, Recommendation
from redrobin.domain.events import Severity


def test_recommendation_to_payload_serializes_severity_and_fields():
    rec = Recommendation(
        suggested_action="Validate svc_reports against SQL01",
        rationale="CrowdStrike observed; a bind auth is quiet vs a loud dump",
        opsec_risk=Severity.WARN,
        confidence="High the cred is valid; unsure the SQL host is in scope",
        references=["vault/Techniques/mssql.md#chunk_0", "type db_backup.config"],
        correlation_id="18ff",
    )
    p = rec.to_payload()
    assert p["opsec_risk"] == "WARN"  # enum -> wire string
    assert p["suggested_action"].startswith("Validate")
    assert p["rationale"].startswith("CrowdStrike")
    assert p["confidence"].startswith("High")
    assert p["references"][0].endswith("#chunk_0")
    assert p["correlation_id"] == "18ff"


def test_recommendation_defaults_are_empty_refs_and_none_corr():
    rec = Recommendation(
        suggested_action="a", rationale="b", opsec_risk=Severity.INFO, confidence="c"
    )
    p = rec.to_payload()
    assert p["references"] == []
    assert p["correlation_id"] is None


def test_agent_tool_is_a_pure_spec():
    async def _handler(args: dict) -> str:
        return "ok"

    tool = AgentTool(
        name="kb_retrieve",
        description="Search tradecraft.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handler,
    )
    assert tool.name == "kb_retrieve"
    assert tool.input_schema["type"] == "object"
