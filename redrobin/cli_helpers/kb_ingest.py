"""KB ingestion helper: read a file, chunk it, embed it, upsert chunks.

Designed so the CLI can compose any embedder + store that match the
``EmbedderProtocol`` / ``StoreProtocol`` shapes below. The real CLI wires
``VoyageClient`` and ``VectorStore`` through thin adapters defined in
``redrobin/cli.py``; tests inject fakes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

from redrobin.infrastructure.kb_indexer import chunk_markdown

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200


class EmbedderProtocol(Protocol):
    async def embed_documents(
        self, texts: list[str]
    ) -> list[list[float]]:  # pragma: no cover - protocol
        ...


class StoreProtocol(Protocol):
    async def upsert(
        self,
        *,
        doc_id: str,
        chunk_id: str,
        text: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:  # pragma: no cover - protocol
        ...


def _doc_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]


async def ingest_file(
    *,
    path: Path,
    tags: list[str],
    embedder: EmbedderProtocol,
    store: StoreProtocol,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    """Read ``path``, chunk it, embed each chunk, upsert one record per chunk.

    Returns the number of chunks successfully indexed.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(text, size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        return 0

    vectors = await embedder.embed_documents(chunks)
    doc_id = _doc_id_for(path)
    for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        chunk_id = f"{doc_id}_chunk_{i}"
        metadata: dict[str, Any] = {
            "source": str(path),
            "doc_id": doc_id,
            "chunk_index": i,
            "tags": list(tags),
        }
        await store.upsert(
            doc_id=doc_id,
            chunk_id=chunk_id,
            text=chunk,
            vector=vector,
            metadata=metadata,
        )
    return len(chunks)
