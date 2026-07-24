import pytest

from redrobin.application.agents.assistant.llm import AgentLLM, build_agent_llm


class _StubAgentLLM:
    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        return "{}"


def test_stub_conforms_to_protocol():
    llm: AgentLLM = _StubAgentLLM()
    assert hasattr(llm, "run")


@pytest.mark.asyncio
async def test_stub_run_returns_str():
    out = await _StubAgentLLM().run(system="s", user_message="u", tools=[])
    assert isinstance(out, str)


def test_build_agent_llm_degraded_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert build_agent_llm(model="claude-haiku-4-5") is None
