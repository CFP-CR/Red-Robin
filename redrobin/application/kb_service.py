"""redrobin/application/kb_service.py — KB orchestrator (index + retrieve).

Coordinates VoyageClient (embeddings) + VectorStore (similarity search).
Called by Layer B engine for RAG context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from redrobin.infrastructure.kb_indexer import chunk_markdown, walk_markdown_files
from redrobin.infrastructure.llm.voyage_client import VoyageClient
from redrobin.infrastructure.vector_store import VectorStore


class KBService:
    def __init__(self, *, voyage: VoyageClient, store: VectorStore):
        self._voyage = voyage
        self._store = store

    async def index_directory(self, root: Path) -> int:
        """Index all .md files under root. Returns number of chunks added."""
        files = walk_markdown_files(root)
        if not files:
            return 0

        all_ids: list[str] = []
        all_texts: list[str] = []
        all_metas: list[dict[str, Any]] = []
        for file_path in files:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            chunks = chunk_markdown(text)
            for i, chunk in enumerate(chunks):
                all_ids.append(f"{file_path}#chunk_{i}")
                all_texts.append(chunk)
                all_metas.append({"source": str(file_path), "chunk_index": i})

        if not all_texts:
            return 0

        result = await self._voyage.embed_documents(all_texts)
        self._store.add(ids=all_ids, texts=all_texts, vectors=result.vectors, metadatas=all_metas)
        return len(all_texts)

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[tuple[str, str, float]]:
        """Return top-K matching chunks. Each entry: (text, source, similarity)."""
        if self._store.count() == 0:
            return []
        query_vec = await self._voyage.embed_query(query)
        hits = self._store.search(query_vector=query_vec, top_k=top_k)
        return [(rec.text, rec.metadata.get("source", ""), score) for rec, score in hits]
