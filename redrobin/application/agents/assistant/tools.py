"""Retrieval tools the assistant hands to the AgentLLM each cycle.

Two PULL tools: ``kb_retrieve`` over ``KBService`` (curated cross-op tradecraft)
and ``search_journal`` over ``JournalService`` (this engagement's verbatim
events.jsonl transcript, kept current by an incremental refresh). ``build_tool_registry``
is the extensibility seam — its returned ``list[AgentTool]`` is what the agent loop
consumes; later tasks append ``get_lessons`` without changing that contract.
``build_kb_service`` / ``build_journal_service`` construct the backends (lazy infra
import, degraded-None without VOYAGE_API_KEY).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from redrobin.application.agents.assistant.lessons import LessonStore
from redrobin.application.journal_service import JournalService
from redrobin.application.kb_service import KBService
from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)

# Return-cap so a large index can never blow the agent's context window (spec
# §6.B refinement 3: cap tool output, return high-signal excerpts not dumps).
_MAX_TOOL_CHARS = 6000
_MAX_JOURNAL_CHARS = 6000
_MAX_LESSONS_CHARS = 6000

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

_SEARCH_JOURNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "A question about something already run or observed in THIS engagement, "
                "e.g. 'the exact output when we dumped LSASS' or 'when did we first see host DC01'."
            ),
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}

_GET_LESSONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "description": (
                "Optional focus, e.g. 'lsass dumping' — filters the operator's past "
                "corrections. Omit to load all lessons that apply to this engagement."
            ),
        }
    },
    "required": [],
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
    except Exception:
        logger.exception("assistant: KB index load failed — kb_retrieve degraded")
        return None


async def build_journal_service() -> JournalService | None:
    """Build + warm the current engagement's Operational Journal (Voyage over the
    joined events.jsonl transcript). Returns None without VOYAGE_API_KEY, with no
    active engagement, or on any indexing failure — the assistant still runs;
    search_journal reports it's unavailable. Async because it warms the backlog at
    boot (off the reactive hot path); the tool keeps it current via retrieve()'s
    incremental refresh. Uses EngagementService().current() — the same source
    events_router uses to decide where events.jsonl is written — so the journal
    reads exactly the file the events were written to. Mirrors build_kb_service."""
    try:
        from redrobin.application.engagement_service import EngagementService
        from redrobin.infrastructure.filesystem import redrobin_root
        from redrobin.infrastructure.llm.voyage_client import VoyageClient
        from redrobin.infrastructure.vector_store import VectorStore

        active = EngagementService().current()
        if active is None:
            logger.info("assistant: no active engagement — search_journal degraded")
            return None
        events_path = redrobin_root() / "engagements" / active / "events.jsonl"
        service = JournalService(
            voyage=VoyageClient(), store=VectorStore(), events_path=events_path
        )
        n = await service.refresh()
        logger.info("assistant: journal indexed %d entries (engagement=%s)", n, active)
        return service
    except RuntimeError:
        logger.info("assistant: VOYAGE_API_KEY missing — search_journal degraded")
        return None
    except Exception:
        logger.exception("assistant: journal indexing failed — search_journal degraded")
        return None


def build_tool_registry(
    kb: KBService | None,
    journal: JournalService | None = None,
    lesson_store: LessonStore | None = None,
    engagement_provider: Callable[[], str | None] | None = None,
) -> list[AgentTool]:
    """The pull-tool set for one cycle: kb_retrieve + search_journal + get_lessons.
    Appending a tool is a pure append — the list[AgentTool] the agent loop consumes
    is the seam (spec §6.F). get_lessons closes the L1-written-but-never-read gap."""

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

    async def _search_journal(args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "No query provided."
        if journal is None:
            return (
                "Operational journal unavailable (no transcript indexed / VOYAGE_API_KEY not set)."
            )
        try:
            hits = await journal.retrieve(query, top_k=5)
        except Exception:
            logger.exception("assistant: search_journal failed for query=%s", query)
            return "Operational journal retrieval failed."
        if not hits:
            return "No matching journal entries found."
        lines = [f"[source: {source} | score={score:.2f}]\n{text}" for text, source, score in hits]
        return "\n\n".join(lines)[:_MAX_JOURNAL_CHARS]

    async def _get_lessons(args: dict[str, Any]) -> str:
        if lesson_store is None:
            return "No operator lessons available."
        try:
            engagement_id = engagement_provider() if engagement_provider is not None else None
            lessons = lesson_store.list_operator_lessons(engagement_id=engagement_id)
        except Exception:
            logger.exception("assistant: get_lessons failed")
            return "Operator-lesson retrieval failed."
        topic = str(args.get("topic", "")).strip().lower()
        if topic:
            lessons = [
                le
                for le in lessons
                if topic in (le.context + " " + le.lesson + " " + le.why).lower()
            ]
        if not lessons:
            return "No operator lessons apply here yet."
        lines = [
            f"[{le.scope} · {le.created_at.date()}] WHEN {le.context}: {le.lesson} (why: {le.why})"
            for le in lessons
        ]
        return "\n".join(lines)[:_MAX_LESSONS_CHARS]

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
        ),
        AgentTool(
            name="search_journal",
            description=(
                "Search the operational journal — the verbatim transcript of THIS engagement's "
                "past commands and their exact output. CALL THIS whenever you need the precise "
                "text of something already run or seen (a prior command's output, when a host or "
                "credential was first observed, what a tool actually returned) instead of relying "
                "on the distilled working-memory summary — then cite the returned journal sources."
            ),
            input_schema=_SEARCH_JOURNAL_SCHEMA,
            handler=_search_journal,
        ),
        AgentTool(
            name="get_lessons",
            description=(
                "Load the operator's past corrections that apply to THIS engagement (their "
                "preferences + this op's context-scoped lessons). ALWAYS call this before "
                "recommending or answering, so you honour what the operator already taught you "
                "and never re-suggest something they rejected — then cite the lesson you applied."
            ),
            input_schema=_GET_LESSONS_SCHEMA,
            handler=_get_lessons,
        ),
    ]


__all__ = ["build_journal_service", "build_kb_service", "build_tool_registry"]
