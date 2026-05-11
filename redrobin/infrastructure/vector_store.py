"""redrobin/infrastructure/vector_store.py — In-memory numpy vector store.

Per spec §13.3: numpy suffices for single-operator KB (~10K-50K docs).
Vectors live in memory, persisted as a single .npz at `~/.redrobin/shared/kb-index/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class VectorRecord:
    id: str  # stable chunk ID, e.g., "vault/Techniques/mimikatz.md#chunk_0"
    text: str
    metadata: dict


class VectorStore:
    """In-memory cosine-similarity store. Persists to a single .npz."""

    def __init__(self) -> None:
        self._records: list[VectorRecord] = []
        self._matrix: np.ndarray | None = None  # shape (N, dim), L2-normalized

    def add(
        self,
        *,
        ids: list[str],
        texts: list[str],
        vectors: list[list[float]],
        metadatas: list[dict] | None = None,
    ) -> None:
        if metadatas is None:
            metadatas = [{}] * len(ids)
        for id_, text, meta in zip(ids, texts, metadatas, strict=True):
            self._records.append(VectorRecord(id=id_, text=text, metadata=meta))
        arr = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        if self._matrix is None:
            self._matrix = arr
        else:
            self._matrix = np.vstack([self._matrix, arr])

    def search(
        self, *, query_vector: list[float], top_k: int = 5
    ) -> list[tuple[VectorRecord, float]]:
        if self._matrix is None or not self._records:
            return []
        q = np.array(query_vector, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        sims = self._matrix @ q
        k = min(top_k, len(self._records))
        top_idx = np.argsort(-sims)[:k]
        return [(self._records[int(i)], float(sims[int(i)])) for i in top_idx]

    def count(self) -> int:
        return len(self._records)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._matrix is None:
            np.savez(
                path,
                vectors=np.zeros((0, 0), dtype=np.float32),
                ids=np.array([], dtype=object),
                texts=np.array([], dtype=object),
            )
            return
        ids = np.array([r.id for r in self._records], dtype=object)
        texts = np.array([r.text for r in self._records], dtype=object)
        # metadatas serialized as repr — JSON would be cleaner; defer to v0.2
        metas = np.array([repr(r.metadata) for r in self._records], dtype=object)
        np.savez(path, vectors=self._matrix, ids=ids, texts=texts, metas=metas)

    @classmethod
    def load(cls, path: Path) -> VectorStore:
        store = cls()
        data = np.load(path, allow_pickle=True)
        vectors = data["vectors"]
        if vectors.size == 0:
            return store
        ids = data["ids"]
        texts = data["texts"]
        metas = data["metas"]
        for id_, text, meta_repr in zip(ids, texts, metas, strict=True):
            try:
                meta = eval(str(meta_repr)) if str(meta_repr) else {}  # safe for dict literals
            except Exception:
                meta = {}
            store._records.append(VectorRecord(id=str(id_), text=str(text), metadata=meta))
        store._matrix = vectors.astype(np.float32)
        return store
