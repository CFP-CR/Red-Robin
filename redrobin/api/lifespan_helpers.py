"""Lifespan decomposition: one async function per startup concern.

Each helper takes ``(app: FastAPI, settings: Settings)`` and returns
``(resource, close)`` where ``close`` is an ``Awaitable[None]``-returning
no-arg callable, or ``None`` when the helper does not manage a closeable
resource. The orchestrator in :mod:`redrobin.api.app` calls them in
order, collects the close hooks, and invokes them in reverse during
shutdown.

Started life as a mechanical extraction of the prior monolithic
``lifespan()`` body. Removing the synchronous OPSEC pre-flight gate
retired three helpers (the verdict pipeline, the Sigma rules loader, and
the lesson promoter); removing the outbound CS REST dispatch retired the
adapter starter — what remains is the capture-and-advise path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import yaml  # type: ignore[import-untyped]
from fastapi import FastAPI

from redrobin.application.config import Settings
from redrobin.application.dedup_keys import DedupKeys, DedupKeysRepo
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.llm import build_agent_llm
from redrobin.application.agents.assistant.tools import (
    build_journal_service,
    build_kb_service,
    build_tool_registry,
)
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.agents.enricher.enricher import WorkingMemoryEnricher
from redrobin.application.agents.enricher.llm import build_enricher_llm
from redrobin.application.chat import ChatHistoryRepo
from redrobin.application.chat.history import build_history_reader
from redrobin.application.chat.recorder import ChatRecorder
from redrobin.application.engagement_loader import Engagement, EngagementLoader
from redrobin.application.engagement_service import EngagementService
from redrobin.application.event_bus import EventBus
from redrobin.application.longterm_lifecycle import (
    ContextSummaryRepo,
    EpisodicRepo,
    LongtermRepo,
    build_summarizer_llm,
)
from redrobin.application.agents.summarizer import L5Summarizer
from redrobin.application.working_memory_lifecycle import WorkingMemory, WorkingMemoryRepo
from redrobin.application.working_memory_reducer import WorkingMemoryReducer
from redrobin.application.working_memory_seed import apply_engagement_scope
from redrobin.domain.memory import working_memory_to_snapshot

logger = logging.getLogger(__name__)

CloseHook = Callable[[], Awaitable[None]]


async def start_event_bus(app: FastAPI, settings: Settings) -> tuple[EventBus, CloseHook]:
    """Construct the in-memory event bus and register it on ``app.state``.

    Returned close hook calls :meth:`EventBus.close`, which broadcasts the
    sentinel that signals every subscriber (reducer, enricher, assistant,
    chat recorder, TUI WS) to drain.
    """
    event_bus = EventBus()
    app.state.event_bus = event_bus

    async def close() -> None:
        await event_bus.close()

    return event_bus, close


async def start_working_memory(
    app: FastAPI, settings: Settings
) -> tuple[WorkingMemoryRepo, CloseHook]:
    """Build the working memory repo, seed empty state, start the reducer.

    Reducer subscribes to ``app.state.event_bus`` (set by
    ``start_event_bus``) and mutates working memory + episodic log on
    every event. We wait for ``reducer.on_started`` before returning so
    the first event published after lifespan startup is guaranteed to be
    observed.

    Engagement isolation: ``start_engagement`` runs FIRST in the lifespan
    so ``app.state.engagement`` is already resolved by the time this
    helper builds the reducer. When an engagement is active, working
    memory + episodic paths resolve to
    ``engagements/<slug>/{working_memory.json, episodic.db}`` and the
    reducer captures the engagement's slug. When no engagement is
    resolved (degraded mode), paths fall back to ``settings.*_path`` so
    tests/CI that don't set up an engagement keep their existing
    behavior. ``longterm`` and ``summary`` repos stay at root paths by
    design — they're cross-engagement shared state.

    Close hook waits up to 2 s for the reducer to drain, then cancels.
    Matches the original lifespan timeout exactly.
    """
    # Engagement isolation: when start_engagement (which now runs first in
    # the lifespan) has resolved an active engagement, point working_memory
    # + episodic at engagements/<slug>/ so each engagement has isolated
    # state. Degraded mode (no engagement resolved) falls back to the
    # root paths from settings — preserves backwards compat for tests/CI
    # that don't set up an engagement.
    active_engagement = getattr(app.state, "engagement", None)
    if active_engagement is not None:
        engagement_id = active_engagement.slug
        eng_dir = settings.engagements_dir / engagement_id
        assert eng_dir.exists(), (
            f"start_engagement resolved {engagement_id} but {eng_dir} "
            f"does not exist — invariant violated"
        )
        wm_path = eng_dir / "working_memory.json"
        ep_path = eng_dir / "episodic.db"
    else:
        engagement_id = settings.engagement_id
        wm_path = settings.working_memory_path
        ep_path = settings.episodic_path
    assert wm_path is not None and ep_path is not None  # _derive_paths or branch above
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    working_memory_repo = WorkingMemoryRepo(wm_path)
    app.state.working_memory_repo = working_memory_repo
    app.state.engagement_id = engagement_id

    # Seed working memory. With an active engagement, project its client + scope
    # onto the WM (#156) so the TUI shows real scope, not (none) — refreshed on
    # every load since engagement.yaml is the source of truth. Degraded mode (no
    # engagement) just seeds an empty WM if missing, preserving prior behavior.
    if active_engagement is not None:
        wm = (
            working_memory_repo.load()
            if working_memory_repo.exists()
            else WorkingMemory(engagement_id=engagement_id)
        )
        working_memory_repo.save(apply_engagement_scope(wm, active_engagement))
    elif not working_memory_repo.exists():
        working_memory_repo.save(WorkingMemory(engagement_id=engagement_id))

    # Longterm + summary repos stay at root paths by design — they're
    # cross-engagement shared state (long-term lessons + kb summaries).
    lt_path = settings.longterm_path
    summary_path = settings.summary_path
    assert lt_path is not None and summary_path is not None  # _derive_paths guarantees
    episodic_repo = EpisodicRepo(ep_path)
    longterm_repo = LongtermRepo(lt_path)
    summary_repo = ContextSummaryRepo(summary_path)
    app.state.episodic_repo = episodic_repo
    app.state.longterm_repo = longterm_repo
    app.state.summary_repo = summary_repo

    reducer = WorkingMemoryReducer(
        bus=app.state.event_bus,
        repo=working_memory_repo,
        engagement_id=engagement_id,
        episodic_repo=episodic_repo,
    )
    started = asyncio.Event()
    reducer.on_started = started.set  # type: ignore[attr-defined]
    reducer_task = asyncio.create_task(reducer.run(), name="working_memory_reducer")
    await started.wait()  # ensure subscription is active before yielding
    app.state.reducer_task = reducer_task

    async def close() -> None:
        # Wait for the reducer to drain and exit cleanly. The bus close
        # (event_bus close hook) is what signals it to exit; we just
        # block on the task completing.
        try:
            await asyncio.wait_for(reducer_task, timeout=2.0)
        except asyncio.TimeoutError:
            reducer_task.cancel()

    return working_memory_repo, close


async def start_engagement(app: FastAPI, settings: Settings) -> tuple[None, None]:
    """Load ``engagement.yaml`` (if configured) onto ``app.state``.

    Engagement resolution precedence (highest -> lowest):
      1. Env var ``REDROBIN_ENGAGEMENT_SLUG`` (explicit override; CI/tests)
      2. ``~/.redrobin/.active_engagement`` file (operator's persistent
         choice via ``redrobin engagement use <slug>``)
      3. None - degraded, engagement-scoped state falls back to root paths

    Without this fallback the daemon ignored the CLI's persistent active
    marker, which silently no-op'd ``engagement use`` for any operator
    not also exporting the env var.

    Stores the loaded engagement on ``app.state.engagement``. The scope
    *data* (``in_scope`` / ``out_of_scope``) is projected onto working
    memory by ``apply_engagement_scope`` and read from there by the
    assistant — nothing enforces it synchronously any more, so no checker
    object is built here.

    No closeable resource - returns ``(None, None)``. The caller's reverse
    iteration skips ``None`` close hooks.
    """
    engagements_dir = settings.engagements_dir
    assert engagements_dir is not None  # Settings._derive_paths guarantees this
    engagement_slug = settings.engagement_slug
    if engagement_slug is None:
        # Fall back to the persistent active marker that `redrobin engagement
        # use` writes. EngagementService is the single source of truth for
        # this file (see ACTIVE_FILE in engagement_service.py).
        engagement_slug = EngagementService(settings.root).current()
    engagement: Engagement | None = None
    if engagement_slug:
        try:
            engagement = EngagementLoader(engagements_dir).load(engagement_slug)
        except FileNotFoundError:
            logger.warning(
                "engagement slug %s set but file not found under %s — "
                "falling back to wildcard scope",
                engagement_slug,
                engagements_dir,
            )
            engagement = None
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            # Broadened catch (#137) — malformed YAML, permission errors,
            # encoding issues should NOT crash uvicorn at startup. Fall
            # back to wildcard scope and warn loudly so the operator sees
            # the problem in the banner / logs without losing daemon
            # availability. FileNotFoundError stays a separate case above
            # because its log message is specific to "file missing".
            logger.warning(
                "engagement slug %s present but engagement.yaml is "
                "unreadable / malformed (%s: %s) — falling back to "
                "wildcard scope. Fix the YAML and restart to recover.",
                engagement_slug,
                type(exc).__name__,
                exc,
            )
            engagement = None

    if engagement is None:
        app.state.engagement = None
        app.state.scope_empty = False
    else:
        app.state.engagement = engagement
        # Keep app.state.engagement_id consistent with the loaded slug so the
        # working-memory reducer + downstream consumers see a single ID.
        app.state.engagement_id = engagement.slug
        # #142: an engagement with empty in_scope carries no target boundary at
        # all, so the assistant reasons without one. Nothing blocks any more —
        # but the operator still needs to know their scope is undefined, so
        # surface it via state (banner) + a WARNING log.
        app.state.scope_empty = not engagement.in_scope
        if app.state.scope_empty:
            logger.warning(
                "engagement %r loaded with EMPTY in_scope - the assistant has "
                "no target boundary to reason about. Set scope: `redrobin "
                "engagement new <slug> --in-scope <host/cidr>` or edit "
                "%s/%s/engagement.yaml and restart the daemon.",
                engagement.slug,
                engagements_dir,
                engagement.slug,
            )

    return None, None


async def start_l5_summarizer(app: FastAPI, settings: Settings) -> tuple[L5Summarizer, CloseHook]:
    """Schedule the periodic L5 sliding-window summariser.

    Slice 13 — reads recent episodes from the same ``episodic_repo`` the
    reducer writes to, compresses them via the cheap summariser model,
    and appends one row per window to the ``summary_repo``.

    The consumer is the **assistant**: ``start_assistant``'s ``_read_summary``
    closure reads the newest row back (``list_recent(engagement_id, limit=1)``)
    into every push-context, so the story-so-far survives older commands rolling
    off the in-memory window. The pre-flight gate's Layer B used to read these
    rows too; it was deleted, so the assistant is now the only reader.

    Runs as a standalone ``l5_summarizer`` task — the close hook below is the
    only thing that stops it, and ``app.py`` awaits it before ``close_bus()``
    so no tail summarisation races the shutdown.

    Close hook cancels the loop and waits up to 2 s for it to exit.
    """
    episodic_repo = app.state.episodic_repo
    summary_repo = app.state.summary_repo
    engagement_id = app.state.engagement_id

    summarizer = L5Summarizer(
        episodic_repo=episodic_repo,
        summary_repo=summary_repo,
        llm=build_summarizer_llm(),
        engagement_id=engagement_id,
        window_size=settings.l5_window,
    )
    app.state.summarizer = summarizer
    summarizer_interval = settings.l5_interval_sec

    async def _summarizer_loop() -> None:
        try:
            while True:
                await asyncio.sleep(summarizer_interval)
                try:
                    await summarizer.run_once()
                except Exception:
                    logger.exception("L5 summariser run failed")
        except asyncio.CancelledError:
            return

    summarizer_task = asyncio.create_task(_summarizer_loop(), name="l5_summarizer")
    app.state.summarizer_task = summarizer_task

    async def close() -> None:
        summarizer_task.cancel()
        try:
            await asyncio.wait_for(summarizer_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    return summarizer, close


async def start_enricher(
    app: FastAPI, settings: Settings
) -> tuple[WorkingMemoryEnricher, CloseHook]:
    """Start the working-memory LLM enricher (Plan B).

    Subscribes to ``beacon.command_output`` and publishes ``working_memory.deltas``
    events the reducer (started earlier) merges. When ANTHROPIC_API_KEY is missing,
    ``build_enricher_llm`` returns None and ``_enrich`` returns immediately on every
    event — the daemon still boots, but this enricher is the ONLY producer of
    ``working_memory.deltas``, so nothing extracts facts from command output at all.
    Working memory then holds only the engagement definition seeded at boot by
    ``apply_engagement_scope`` (client/scope/ROE/objectives) plus what the reducer
    takes straight off the wire (``beacon.initial`` → ``Session``); hosts,
    credentials and findings stay empty.
    There is no fallback extractor — Plan B2 retired the command-output regex
    producer, so degraded here means no enrichment, not reduced enrichment.
    """
    enricher = WorkingMemoryEnricher(
        bus=app.state.event_bus,
        llm=build_enricher_llm(model=settings.enrichment_model),
        denylist=settings.enrichment_denylist,
        min_output_chars=settings.enrichment_min_output_chars,
        chunk_max_chars=settings.enrichment_chunk_max_chars,
        enabled=settings.enrichment_enabled,
        max_concurrency=settings.enrichment_max_concurrency,
        self_annotation_prefix=settings.enrichment_self_annotation_prefix,
    )
    started = asyncio.Event()
    enricher.on_started = started.set  # type: ignore[attr-defined]
    enricher_task = asyncio.create_task(enricher.run(), name="wm_enricher")
    await started.wait()
    app.state.enricher = enricher
    app.state.enricher_task = enricher_task

    async def close() -> None:
        try:
            await asyncio.wait_for(enricher_task, timeout=2.0)
        except asyncio.TimeoutError:
            enricher_task.cancel()

    return enricher, close


async def start_assistant(app: FastAPI, settings: Settings) -> tuple[Assistant, CloseHook]:
    """Start the reactive AI assistant (Task 2).

    Subscribes to the bus, consumes ``enrichment.completed``, and publishes
    ``advisor.recommendation``. Degraded-None: missing ANTHROPIC_API_KEY ->
    ``build_agent_llm`` returns None and the assistant stays silent; missing
    VOYAGE_API_KEY -> ``build_kb_service`` and ``build_journal_service`` return None
    and kb_retrieve / search_journal degrade; either way the daemon still boots.
    Mirrors ``start_enricher``.

    Reads the L5 summary via a closure over ``app.state.summary_repo`` (the
    ContextSummaryRepo the summariser writes) so the push-context carries the
    story-so-far without coupling the assistant to persistence.
    """
    summary_repo = app.state.summary_repo
    engagement_id = app.state.engagement_id

    # Re-read the active engagement_id off ``app.state`` each call so a future
    # engagement switch surfaces the right summary without rebuilding the assistant.
    # The captured value is only a fallback if the attribute is ever missing.
    def _engagement_id_provider() -> str:
        return getattr(app.state, "engagement_id", engagement_id)

    def _read_summary() -> str:
        try:
            rows = summary_repo.list_recent(_engagement_id_provider(), limit=1)
        except Exception:
            logger.exception("assistant: failed reading L5 summary")
            return ""
        return rows[0].text if rows else ""

    def _read_wm_snapshot() -> dict[str, Any]:
        # #30: seed the assistant's L2 from the persisted WM at boot so it is NOT blind to
        # existing engagement state until the first mutating event. Reads app.state each call
        # (mirrors _read_summary) and never raises — an empty dict is the safe fallback.
        try:
            repo = getattr(app.state, "working_memory_repo", None)
            if repo is None or not repo.exists():
                return {}
            return working_memory_to_snapshot(repo.load())
        except Exception:
            logger.exception("assistant: failed reading initial WM snapshot")
            return {}

    # Only load the KB index + warm the journal when the assistant is actually
    # enabled — otherwise a boot with a real VOYAGE_API_KEY would load the whole
    # kb-index and embed the entire events.jsonl backlog for a turned-off feature
    # (and delay the adapters/C2 boundary that starts after us).
    kb = build_kb_service() if settings.assistant_enabled else None
    journal = await build_journal_service() if settings.assistant_enabled else None
    lesson_store = getattr(
        app.state, "longterm_repo", None
    )  # set by start_working_memory, runs earlier

    # Chat transcript (spec 2026-07-24). Gated on assistant_enabled like kb/journal: with
    # the assistant off there is no stream to record. The path reuses the engagement
    # directory start_working_memory already resolved (it also handles the degraded
    # no-engagement fallback), so the resolution logic is not duplicated here.
    history_reader: Callable[[], str] | None = None
    chat_task: asyncio.Task[None] | None = None
    if settings.assistant_enabled:
        wm_repo = getattr(app.state, "working_memory_repo", None)
        if wm_repo is not None:
            chat_repo = ChatHistoryRepo(wm_repo.path.parent / "chat_history.jsonl")
            app.state.chat_history_repo = chat_repo
            history_reader = build_history_reader(
                chat_repo,
                dialogue_max=settings.assistant_chat_dialogue_turns,
                volunteered_max=settings.assistant_chat_advisory_turns,
            )
            chat_recorder = ChatRecorder(bus=app.state.event_bus, repo=chat_repo)
            chat_started = asyncio.Event()
            chat_recorder.on_started = chat_started.set
            chat_task = asyncio.create_task(chat_recorder.run(), name="chat-recorder")
            await chat_started.wait()
            app.state.chat_recorder = chat_recorder
            app.state.chat_recorder_task = chat_task

    assistant = Assistant(
        bus=app.state.event_bus,
        llm=build_agent_llm(model=settings.assistant_model),
        tools=build_tool_registry(
            kb,
            journal=journal,
            lesson_store=lesson_store,
            engagement_provider=_engagement_id_provider,
        ),
        trigger=TriggerGate(cooldown_sec=settings.assistant_cooldown_sec),
        summary_reader=_read_summary,
        snapshot_reader=_read_wm_snapshot,
        history_reader=history_reader,
        enabled=settings.assistant_enabled,
        max_tokens=settings.assistant_max_tokens,
        wm_max_items=settings.assistant_wm_max_items,
        lull_sec=settings.assistant_lull_sec,
        proactive_min_interval_sec=settings.assistant_proactive_min_interval_sec,
        lesson_store=lesson_store,
        engagement_provider=_engagement_id_provider,
    )
    started = asyncio.Event()
    assistant.on_started = started.set  # type: ignore[attr-defined]
    assistant_task = asyncio.create_task(assistant.run(), name="assistant")
    await started.wait()
    app.state.assistant = assistant
    app.state.assistant_task = assistant_task

    async def close() -> None:
        # Both tasks exit on the bus ``_close`` sentinel, which app.py broadcasts BEFORE
        # calling this hook, so normally both are already draining and this returns at
        # once. They are awaited CONCURRENTLY under a SINGLE timeout so that a wedged
        # task bounds the shutdown stall at 2 s total, not 2 s per task.
        pending_tasks = [t for t in (assistant_task, chat_task) if t is not None]
        done, pending = await asyncio.wait(pending_tasks, timeout=2.0)
        # ``asyncio.wait`` does NOT retrieve exceptions from the done set (unlike the
        # ``wait_for`` this replaced, which re-raised into app.py's "close_assistant
        # failed" log). ``Assistant.run`` guards per-event exceptions, so a crash at the
        # LOOP level surfaces here and nowhere else — retrieve it explicitly. Both tasks
        # are NAMED at creation ("assistant" / "chat-recorder"), so log the name: one line
        # covering two loops is only diagnostic if it says which one died.
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.error(
                    "assistant/chat task %r ended with an exception",
                    task.get_name(),
                    exc_info=task.exception(),
                )
        for task in pending:
            task.cancel()

    return assistant, close


async def start_dedup(app: FastAPI, settings: Settings) -> tuple[DedupKeys, CloseHook]:
    """Load the per-engagement dedup-key set into ``app.state.dedup_keys``.

    Reads the engagement already resolved by ``start_engagement`` (which
    handles the ``REDROBIN_ENGAGEMENT_SLUG`` env var → ``.active_engagement``
    file precedence). MUST be called AFTER ``start_engagement`` so that
    ``app.state.engagement`` is populated — calling it earlier silently falls
    to the degraded path and writes the dedup file under root instead of
    under the engagement dir.

    Degraded path (no engagement resolved): a DedupKeys backed by a per-root
    file (``<root>/dedup_keys.json``) so the events router can always call
    into ``app.state.dedup_keys`` without a None-check.
    """
    active_engagement = getattr(app.state, "engagement", None)
    if active_engagement is not None and settings.engagements_dir is not None:
        keys_path = settings.engagements_dir / active_engagement.slug / "dedup_keys.json"
    else:
        keys_path = settings.root / "dedup_keys.json"
    repo = DedupKeysRepo(keys_path)
    dedup_keys = DedupKeys.load(repo)
    app.state.dedup_keys = dedup_keys
    logger.info("dedup_keys: loaded %d keys from %s", len(dedup_keys), keys_path)

    async def _close_dedup() -> None:
        # No background tasks / sockets to close; in-memory + disk state is
        # already durable. A no-op closer matches the existing helper
        # convention — every other resource-returning helper provides a real
        # or no-op CloseHook (never ``None``).
        return None

    return dedup_keys, _close_dedup


__all__ = [
    "CloseHook",
    "start_assistant",
    "start_dedup",
    "start_engagement",
    "start_enricher",
    "start_event_bus",
    "start_l5_summarizer",
    "start_working_memory",
]
