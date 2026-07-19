"""Retrieval tools the assistant hands to the AgentLLM each cycle.

Task 2 (#23) ships one PULL tool — ``kb_retrieve`` over ``KBService`` (the built,
curated tradecraft layer). ``build_tool_registry`` is the extensibility seam:
later tasks append ``search_journal`` / ``get_lessons`` without touching the
signature. ``build_kb_service`` mirrors ``verdict_pipeline_factory``'s KB
construction (lazy infra import, degraded-None without VOYAGE_API_KEY).
"""

from __future__ import annotations

import logging
from typing import Any

from redrobin.application.kb_service import KBService
from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)

# Return-cap so a large index can never blow the agent's context window (spec
# §6.B refinement 3: cap tool output, return high-signal excerpts not dumps).
_MAX_TOOL_CHARS = 6000

_KB_RETRIEVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "A tradecraft / technique / OPSEC question, e.g. 'quiet ways to "
                "validate a MSSQL service credential without triggering EDR'."
            ),
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}


def build_kb_service() -> KBService | None:
    """Build the shared KB retrieval backend (Voyage embeddings + persisted numpy
    VectorStore at ~/.redrobin/shared/kb-index). Returns None when VOYAGE_API_KEY
    is missing (degraded — the assistant still runs; kb_retrieve reports it's
    unavailable). Mirrors verdict_pipeline_factory.build_default_dependencies."""
    try:
        from redrobin.infrastructure.filesystem import redrobin_root
        from redrobin.infrastructure.llm.voyage_client import VoyageClient
        from redrobin.infrastructure.vector_store import VectorStore

        kb_index_path = redrobin_root() / "shared" / "kb-index" / "index.npz"
        store = VectorStore.load(kb_index_path) if kb_index_path.exists() else VectorStore()
        return KBService(voyage=VoyageClient(), store=store)
    except RuntimeError:
        logger.info(
            "assistant: VOYAGE_API_KEY missing — kb_retrieve degraded (no tradecraft retrieval)"
        )
        return None


def build_tool_registry(kb: KBService | None) -> list[AgentTool]:
    """The pull-tool set for one cycle. Task 2: only ``kb_retrieve``."""

    async def _kb_retrieve(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "No query provided."
        if kb is None:
            return "Knowledge base unavailable (VOYAGE_API_KEY not set)."
        try:
            hits = await kb.retrieve(query, top_k=5)
        except Exception:
            logger.exception("assistant: kb_retrieve failed for query=%s", query)
            return "Knowledge base retrieval failed."
        if not hits:
            return "No matching tradecraft found."
        lines = [f"[source: {source} | score={score:.2f}]\n{text}" for text, source, score in hits]
        return "\n\n".join(lines)[:_MAX_TOOL_CHARS]

    return [
        AgentTool(
            name="kb_retrieve",
            description=(
                "Search the curated red-team knowledge base for tradecraft, technique, and "
                "OPSEC guidance. CALL THIS whenever a recommendation depends on how a technique "
                "works, how to run it quietly, or how a control detects it — then cite the "
                "returned sources in your references."
            ),
            input_schema=_KB_RETRIEVE_SCHEMA,
            handler=_kb_retrieve,
        )
    ]


__all__ = ["build_kb_service", "build_tool_registry"]
