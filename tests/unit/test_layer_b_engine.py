"""Layer B engine — LLM judge with RAG, JSON verdict parsing."""
from unittest.mock import AsyncMock

import pytest

from redrobin.application.kb_service import KBService
from redrobin.application.layer_b_engine import LayerBEngine
from redrobin.domain.llm import LLMResponse
from redrobin.domain.opsec import VerdictAction


def _mock_llm(text: str) -> AsyncMock:
    llm = AsyncMock()
    llm.complete_with_cache = AsyncMock(
        return_value=LLMResponse(
            text=text,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=10,
        )
    )
    return llm


def _mock_kb(hits=None) -> AsyncMock:
    kb = AsyncMock(spec=KBService)
    kb.retrieve = AsyncMock(return_value=hits or [])
    return kb


@pytest.mark.asyncio
async def test_layer_b_parses_pass_verdict():
    llm = _mock_llm("""```json
{"action": "PASS", "reason": "harmless recon", "suggestions": []}
```""")
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="whoami", raw="whoami")
    assert v.action == VerdictAction.PASS
    assert v.reason == "harmless recon"


@pytest.mark.asyncio
async def test_layer_b_parses_warn_verdict():
    llm = _mock_llm("""```json
{"action": "WARN", "reason": "AMSI risk", "suggestions": ["use powerpick"]}
```""")
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="powershell", raw="powershell -Command 'Get-Process'")
    assert v.action == VerdictAction.WARN
    assert "AMSI" in v.reason
    assert v.suggestions == ["use powerpick"]


@pytest.mark.asyncio
async def test_layer_b_parses_block_verdict():
    llm = _mock_llm("""```json
{"action": "BLOCK", "reason": "lsass dump signature", "suggestions": ["nanodump"]}
```""")
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="mimikatz", raw="mimikatz sekurlsa::logonpasswords")
    assert v.action == VerdictAction.BLOCK
    assert v.suggestions == ["nanodump"]


@pytest.mark.asyncio
async def test_layer_b_retrieves_kb_and_passes_to_llm():
    llm = _mock_llm('```json\n{"action": "PASS", "reason": "ok"}\n```')
    kb = _mock_kb(hits=[("mimikatz info", "vault/mimikatz.md", 0.9), ("dcsync info", "vault/dcsync.md", 0.8)])
    engine = LayerBEngine(llm=llm, kb=kb, top_k_kb_chunks=2)
    await engine.evaluate(command="mimikatz", raw="mimikatz logonpasswords")
    kb.retrieve.assert_awaited_once_with("mimikatz logonpasswords", top_k=2)
    call = llm.complete_with_cache.await_args
    assert call.kwargs["cacheable_kb_chunks"] == ["mimikatz info", "dcsync info"]


@pytest.mark.asyncio
async def test_layer_b_includes_engagement_summary_in_dynamic_state():
    llm = _mock_llm('```json\n{"action": "WARN", "reason": "x"}\n```')
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    engine.set_engagement_summary("DC01: Win2022, CrowdStrike active, SYSTEM access")
    await engine.evaluate(command="mimikatz", raw="mimikatz")
    call = llm.complete_with_cache.await_args
    assert "DC01" in call.kwargs["dynamic_state"]
    assert "CrowdStrike" in call.kwargs["dynamic_state"]


@pytest.mark.asyncio
async def test_layer_b_handles_unparseable_response_with_fail_open():
    llm = _mock_llm("not JSON at all, just prose")
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="x", raw="x")
    assert v.action == VerdictAction.PASS
    assert "unparseable" in v.reason


@pytest.mark.asyncio
async def test_layer_b_handles_unknown_action_with_fail_open():
    llm = _mock_llm('```json\n{"action": "UNKNOWN", "reason": "x"}\n```')
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="x", raw="x")
    assert v.action == VerdictAction.PASS


@pytest.mark.asyncio
async def test_layer_b_handles_kb_retrieval_failure():
    llm = _mock_llm('```json\n{"action": "PASS", "reason": "ok"}\n```')
    kb = _mock_kb()
    kb.retrieve = AsyncMock(side_effect=RuntimeError("KB broken"))
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="x", raw="x")
    # KB failure → empty chunks but LLM still called
    assert v.action == VerdictAction.PASS
    call = llm.complete_with_cache.await_args
    assert call.kwargs["cacheable_kb_chunks"] == []


@pytest.mark.asyncio
async def test_layer_b_handles_llm_failure_with_fail_open():
    llm = AsyncMock()
    llm.complete_with_cache = AsyncMock(side_effect=RuntimeError("API timeout"))
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="x", raw="x")
    assert v.action == VerdictAction.PASS
    assert "Layer B unavailable" in v.reason


@pytest.mark.asyncio
async def test_layer_b_handles_response_without_json_block():
    """LLM returns raw JSON without ```json fences — should still parse."""
    llm = _mock_llm('{"action": "WARN", "reason": "no fences but valid JSON"}')
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    v = await engine.evaluate(command="x", raw="x")
    assert v.action == VerdictAction.WARN
