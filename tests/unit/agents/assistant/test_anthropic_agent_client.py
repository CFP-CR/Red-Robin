import pytest

from redrobin.domain.assistant import AgentTool
from redrobin.infrastructure.llm.anthropic_client import _first_text, _to_runner_tool

_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def test_to_runner_tool_carries_name_description_schema():
    async def _handler(args: dict) -> str:
        return "ok"

    tool = AgentTool(
        name="kb_retrieve", description="Search tradecraft.", input_schema=_SCHEMA, handler=_handler
    )
    d = _to_runner_tool(tool).to_dict()
    assert d["name"] == "kb_retrieve"
    assert d["description"] == "Search tradecraft."
    assert d["input_schema"] == _SCHEMA


@pytest.mark.asyncio
async def test_to_runner_tool_handler_receives_kwargs_as_dict():
    seen: dict = {}

    async def _handler(args: dict) -> str:
        seen.update(args)
        return "done"

    tool = AgentTool(name="kb_retrieve", description="d", input_schema=_SCHEMA, handler=_handler)
    runner_tool = _to_runner_tool(tool)
    out = await runner_tool.call({"query": "svc_reports"})
    assert out == "done"
    assert seen == {"query": "svc_reports"}


def test_first_text_returns_first_text_block():
    class _Block:
        def __init__(self, type_: str, text: str = ""):
            self.type = type_
            self.text = text

    class _Msg:
        content = [_Block("tool_use"), _Block("text", "hello")]

    assert _first_text(_Msg()) == "hello"


def test_first_text_empty_when_no_text_block():
    class _Block:
        def __init__(self, type_: str):
            self.type = type_

    class _Msg:
        content = [_Block("tool_use")]

    assert _first_text(_Msg()) == ""
