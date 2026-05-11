"""redrobin/infrastructure/llm/voyage_client.py — Voyage AI embeddings (voyage-4-large).

Per spec §4 Componente 3 + §13.3:
- Used by KB indexer to embed markdown chunks
- Used by KB retriever to embed query strings
- voyage-4-large: 2048 dim default (Matryoshka — also supports 256/512/1024), 32K context

Async via voyageai.AsyncClient. API key from env (VOYAGE_API_KEY) per §6 T2 mitigation.
"""

from __future__ import annotations

from dataclasses import dataclass

import voyageai

from redrobin.infrastructure.llm.api_keys import get_voyage_key


@dataclass(frozen=True)
class EmbeddingsResult:
    vectors: list[list[float]]
    total_tokens: int


class VoyageClient:
    def __init__(
        self,
        *,
        model: str = "voyage-4-large",
        input_type_documents: str = "document",
        input_type_query: str = "query",
    ):
        self._model = model
        self._docs_type = input_type_documents
        self._query_type = input_type_query
        # voyageai SDK reads VOYAGE_API_KEY from env automatically; force resolution
        # via api_keys helper so missing key fails loudly at construction.
        _ = get_voyage_key()
        self._client = voyageai.AsyncClient()

    async def embed_documents(self, texts: list[str]) -> EmbeddingsResult:
        result = await self._client.embed(texts, model=self._model, input_type=self._docs_type)
        return EmbeddingsResult(
            vectors=list(result.embeddings),
            total_tokens=getattr(result, "total_tokens", 0) or 0,
        )

    async def embed_query(self, text: str) -> list[float]:
        result = await self._client.embed([text], model=self._model, input_type=self._query_type)
        return list(result.embeddings[0])
