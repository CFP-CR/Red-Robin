"""The EnricherLLM protocol is structural; a stub conforms, and build_enricher_llm
returns None when the API key is absent (degraded mode)."""

from __future__ import annotations

import pytest

from redrobin.application.agents.enricher.llm import EnricherLLM, build_enricher_llm
from redrobin.infrastructure.llm.anthropic_client import (
    AnthropicEnricherClient,
    _build_enricher_user_message,
)


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


def test_enricher_system_prompt_has_no_hardcoded_victim_username() -> None:
    # #105 layer 1: the static (cached) prompt must NOT embed any specific
    # operator/victim username. It teaches "usernames are not hosts" with a
    # generic example; the live beacon's actual user is injected dynamically
    # per call (see _build_enricher_user_message), never hardcoded here.
    prompt = AnthropicEnricherClient._SYSTEM_PROMPT.lower()
    assert "jcspi" not in prompt
    # The not-a-host teaching is still present.
    assert "username" in prompt
    assert "host" in prompt


def test_build_user_message_without_grounding() -> None:
    msg = _build_enricher_user_message(
        command="whoami", beacon_id="B-1", output="CORP\\jdoe", host=None, user=None
    )
    assert "Command: whoami" in msg
    assert "Output:\nCORP\\jdoe" in msg
    assert "do NOT emit" not in msg  # no grounding line when host/user unknown


def test_build_user_message_with_grounding() -> None:
    msg = _build_enricher_user_message(
        command="dir", beacon_id="B-1", output="...", host="WS-FINANCE-03", user="CORP\\jdoe"
    )
    assert "WS-FINANCE-03" in msg
    assert "CORP\\jdoe" in msg
    assert "do NOT emit" in msg
