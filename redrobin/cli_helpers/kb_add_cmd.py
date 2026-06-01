"""``redrobin kb add`` business logic — extracted in Slice 12.

The CLI wrapper in :mod:`redrobin.cli` loads ``.env``; :func:`run_kb_add` here
wires the Voyage embedder + on-disk :class:`VectorStore` into the generic
``ingest_file`` helper and persists the resulting index to
``~/.redrobin/shared/kb-index/index.npz``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from redrobin.cli_helpers.kb_ingest import ingest_file
from redrobin.infrastructure.filesystem import ensure_layout, redrobin_root
from redrobin.infrastructure.llm.voyage_client import VoyageClient
from redrobin.infrastructure.vector_store import VectorStore


class _VoyageAdapter:
    def __init__(self) -> None:
        self._client = VoyageClient()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = await self._client.embed_documents(texts)
        return list(result.vectors)


class _StoreAdapter:
    def __init__(self, vs: VectorStore) -> None:
        self._vs = vs

    async def upsert(
        self,
        *,
        doc_id: str,
        chunk_id: str,
        text: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        self._vs.add(
            ids=[chunk_id],
            texts=[text],
            vectors=[vector],
            metadatas=[metadata],
        )


def run_kb_add(
    *,
    path: Path,
    tags: list[str],
    chunk_size: int,
    chunk_overlap: int,
) -> int:
    """Ingest a single file, embed each chunk via Voyage, persist to the local index."""
    if not path.exists():
        print(f"[!] {path} does not exist", file=sys.stderr)
        return 1
    if not path.is_file():
        print(f"[!] {path} is not a regular file (kb add expects a single file)", file=sys.stderr)
        return 1

    ensure_layout()
    kb_index_path = redrobin_root() / "shared" / "kb-index" / "index.npz"
    store = VectorStore.load(kb_index_path) if kb_index_path.exists() else VectorStore()

    print(f"[*] KB indexing {path} (tags={tags})")

    try:
        embedder = _VoyageAdapter()
    except RuntimeError as e:
        print(f"[!] {e}", file=sys.stderr)
        print("[!] VOYAGE_API_KEY must be set to index KB content", file=sys.stderr)
        return 1

    n = asyncio.run(
        ingest_file(
            path=path,
            tags=tags,
            embedder=embedder,
            store=_StoreAdapter(store),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    )
    store.save(kb_index_path)
    print(f"[+] Indexed {n} chunk(s) into {kb_index_path}")
    return 0
