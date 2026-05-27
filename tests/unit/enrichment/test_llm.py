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


def test_enricher_system_prompt_grounds_host_concept():
    """Area 4: the enricher prompt must teach the OS-agnostic host concept so a
    username / path / drive label is not extracted as a host. Prompt-only."""
    from redrobin.infrastructure.llm.anthropic_client import AnthropicEnricherClient

    prompt = AnthropicEnricherClient._SYSTEM_PROMPT
    lowered = prompt.lower()
    # defines a host as a machine identity (hostname / FQDN / IP)
    assert "hostname" in lowered
    assert "fqdn" in lowered
    # explicitly excludes the non-host categories that misfired in the smoke
    assert "username" in lowered
    assert "drive" in lowered or "volume" in lowered
    assert "path" in lowered
    assert "service" in lowered
    # carries at least one cross-OS negative example (Windows drive label + a path)
    assert "/etc/passwd" in prompt
