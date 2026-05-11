"""Voyage AI embeddings client (mocked)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from redrobin.infrastructure.llm.voyage_client import VoyageClient


@pytest.fixture
def voyage_env(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")


@pytest.mark.asyncio
async def test_embed_documents_returns_vectors_and_tokens(voyage_env, monkeypatch):
    mock_response = MagicMock()
    mock_response.embeddings = [[0.1, 0.2], [0.3, 0.4]]
    mock_response.total_tokens = 42
    mock_async_client = MagicMock()
    mock_async_client.embed = AsyncMock(return_value=mock_response)

    client = VoyageClient(model="voyage-4-large")
    client._client = mock_async_client  # inject mock

    result = await client.embed_documents(["chunk a", "chunk b"])
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert result.total_tokens == 42

    call = mock_async_client.embed.await_args
    assert call.args == (["chunk a", "chunk b"],)
    assert call.kwargs["model"] == "voyage-4-large"
    assert call.kwargs["input_type"] == "document"


@pytest.mark.asyncio
async def test_embed_query_returns_single_vector(voyage_env):
    mock_response = MagicMock()
    mock_response.embeddings = [[0.5, 0.6, 0.7]]
    mock_response.total_tokens = 3
    mock_async_client = MagicMock()
    mock_async_client.embed = AsyncMock(return_value=mock_response)

    client = VoyageClient()
    client._client = mock_async_client

    vec = await client.embed_query("what is dcsync?")
    assert vec == [0.5, 0.6, 0.7]
    call = mock_async_client.embed.await_args
    assert call.kwargs["input_type"] == "query"


def test_construction_requires_voyage_api_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        VoyageClient()


def test_default_model_is_voyage_4_large(voyage_env):
    client = VoyageClient()
    assert client._model == "voyage-4-large"
