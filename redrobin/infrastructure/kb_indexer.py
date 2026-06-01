"""redrobin/infrastructure/kb_indexer.py — Markdown chunking + embedding pipeline."""

from __future__ import annotations

from pathlib import Path

CHUNK_SIZE_CHARS = 800  # ~200 tokens; voyage-4-large 32K context easily handles this
CHUNK_OVERLAP_CHARS = 100


def chunk_markdown(
    text: str, *, size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[str]:
    """Simple character-window chunker. v0.2 candidate: heading-aware chunker."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def walk_markdown_files(root: Path) -> list[Path]:
    """Walk root recursively, return all .md / .markdown files."""
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.suffix.lower() in {".md", ".markdown"} and p.is_file()]
