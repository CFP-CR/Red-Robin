"""Anthropic client with cache_control structured blocks (spec §13.3)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from redrobin.infrastructure.llm.anthropic_client import AnthropicClient


@pytest.mark.asyncio
async def test_anthropic_client_uses_cache_control_blocks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = AnthropicClient(model="claude-sonnet-4-6")
    mock_anthropic = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="hello")]
    mock_response.usage.cache_creation_input_tokens = 100
    mock_response.usage.cache_read_input_tokens = 50
    mock_response.usage.output_tokens = 5
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
    client._client = mock_anthropic

    response = await client.complete_with_cache(
        stable_system_prompt="You are Red Robin.",
        cacheable_kb_chunks=["chunk1", "chunk2"],
        dynamic_state="<engagement>state</engagement>",
        user_message="What is dcsync?",
    )

    assert response.text == "hello"
    assert response.cache_creation_input_tokens == 100
    assert response.cache_read_input_tokens == 50
    assert response.output_tokens == 5

    call_kwargs = mock_anthropic.messages.create.await_args.kwargs
    system = call_kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 3
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[2]
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_anthropic_client_omits_kb_block_when_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = AnthropicClient()
    mock_anthropic = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="ok")]
    mock_response.usage.cache_creation_input_tokens = 0
    mock_response.usage.cache_read_input_tokens = 0
    mock_response.usage.output_tokens = 1
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
    client._client = mock_anthropic

    await client.complete_with_cache(
        stable_system_prompt="You are Red Robin.",
        cacheable_kb_chunks=[],
        dynamic_state="<state/>",
        user_message="hi",
    )

    system = mock_anthropic.messages.create.await_args.kwargs["system"]
    assert len(system) == 2  # system prompt + state, no KB block


def test_anthropic_client_default_model_is_sonnet_4_6(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = AnthropicClient()
    assert client._model == "claude-sonnet-4-6"
    assert client._summarizer_model == "claude-haiku-4-5"


def test_anthropic_client_uses_max_retries_5(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = AnthropicClient()
    # Verify SDK client was constructed with max_retries=5 (single retry layer, no custom backoff)
    # The actual anthropic.AsyncAnthropic instance is in client._client; the max_retries
    # is on it.
    assert getattr(client._client, "max_retries", None) == 5
