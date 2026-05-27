"""The EnricherLLM protocol is structural; a stub conforms, and build_enricher_llm
returns None when the API key is absent (degraded mode)."""

from __future__ import annotations

import pytest

from redrobin.application.enrichment.llm import EnricherLLM, build_enricher_llm


class _StubLLM:
    async def extract(self, *, command: str, beacon_id: str | None, output: str) -> str:
        return "{}"


def test_stub_conforms_to_protocol():
    llm: EnricherLLM = _StubLLM()
    assert hasattr(llm, "extract")


@pytest.mark.asyncio
async def test_stub_extract_returns_str():
    out = await _StubLLM().extract(command="systeminfo", beacon_id="B-1", output="x")
    assert isinstance(out, str)


def test_build_enricher_llm_degraded_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert build_enricher_llm(model="claude-haiku-4-5") is None
