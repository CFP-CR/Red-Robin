"""Lifespan decomposition: one async function per startup concern.

Each helper takes ``(app: FastAPI, settings: Settings)`` and returns
``(resource, close)`` where ``close`` is an ``Awaitable[None]``-returning
no-arg callable, or ``None`` when the helper does not manage a closeable
resource. The orchestrator in :mod:`redrobin.api.app` calls them in
order, collects the close hooks, and invokes them in reverse during
shutdown.

This module is intentionally a mechanical extraction of the prior
monolithic ``lifespan()`` body. **NO behavior change.** Resource
creation order and close-hook ordering are preserved bit-for-bit so the
slice is a pure refactor. Slice 10 will reorder shutdown
(drain background tasks before adapter close); Slice 11 will touch the
Sigma matcher — both happen on top of this one.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from redrobin.application.config import Settings
from redrobin.application.cs_adapter_lifecycle import build_adapter, load_adapter_configs
from redrobin.application.engagement_loader import Engagement, EngagementLoader
from redrobin.application.engagement_service import EngagementService
from redrobin.application.event_bus import EventBus
from redrobin.application.lesson_promoter import LessonPromoter
from redrobin.application.longterm_lifecycle import (
    ContextSummaryRepo,
    EpisodicRepo,
    LongtermRepo,
    build_summarizer_llm,
)
from redrobin.application.rules_lifecycle import RulesLoader
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.application.summarizer import L5Summarizer
from redrobin.application.verdict_pipeline_factory import (
    build_default_dependencies,
    build_pipeline,
)
from redrobin.application.working_memory_lifecycle import WorkingMemory, WorkingMemoryRepo
from redrobin.application.working_memory_reducer import WorkingMemoryReducer

logger = logging.getLogger(__name__)

CloseHook = Callable[[], Awaitable[None]]


async def start_event_bus(
    app: FastAPI, settings: Settings
) -> tuple[EventBus, CloseHook]:
    """Construct the in-memory event bus and register it on ``app.state``.

    Returned close hook calls :meth:`EventBus.close`, which broadcasts the
    sentinel that signals every subscriber (reducer, TUI WS, pipeline) to
    drain.
    """
    event_bus = EventBus()
    app.state.event_bus = event_bus

    async def close() -> None:
        await event_bus.close()

    return event_bus, close


async def start_rules(
    app: FastAPI, settings: Settings
) -> tuple[RulesLoader, CloseHook]:
    """Load Sigma rules from ``settings.rules_dir`` and start hot-reload.

    Stores the loader plus its initial rule list on
    ``app.state.rules_loader`` / ``app.state.initial_rules`` so later
    helpers (``start_pipeline``) can reuse them without re-reading the
    rules directory.

    The hot-reload callback rewires Layer A on every refresh — but Layer
    A is built later (``start_pipeline``), so we defer
    ``start_hot_reload`` registration into ``start_pipeline`` where the
    callback closure can reference the live engine. Stop hook here just
    calls :meth:`RulesLoader.stop_hot_reload`.
    """
    rules_dir = settings.rules_dir
    assert rules_dir is not None  # Settings._derive_paths guarantees this
    if "REDROBIN_RULES_DIR" not in os.environ:
        logger.warning(
            "REDROBIN_RULES_DIR not set; defaulting to %s — set explicitly in tests/CI to avoid home pollution.",
            rules_dir,
        )
    rules_dir.mkdir(parents=True, exist_ok=True)

    rules_loader = RulesLoader(rule_dir=rules_dir)
    initial_rules = rules_loader.load_all()
    app.state.rules_loader = rules_loader
    app.state.initial_rules = initial_rules

    async def stop() -> None:
        rules_loader.stop_hot_reload()

    return rules_loader, stop


async def start_working_memory(
    app: FastAPI, settings: Settings
) -> tuple[WorkingMemoryRepo, CloseHook]:
    """Build the working memory repo, seed empty state, start the reducer.

    Reducer subscribes to ``app.state.event_bus`` (set by
    ``start_event_bus``) and mutates working memory + episodic log on
    every event. We wait for ``reducer.on_started`` before returning so
    the first event published after lifespan startup is guaranteed to be
    observed.

    Engagement may rewrite ``app.state.engagement_id`` after this helper
    runs — the reducer reads the captured value at construction time and
    that's the documented contract.

    Close hook waits up to 2 s for the reducer to drain, then cancels.
    Matches the original lifespan timeout exactly.
    """
    wm_path = settings.working_memory_path
    assert wm_path is not None  # Settings._derive_paths guarantees this
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    working_memory_repo = WorkingMemoryRepo(wm_path)
    app.state.working_memory_repo = working_memory_repo

    engagement_id = settings.engagement_id
    app.state.engagement_id = engagement_id

    # Seed an empty working memory file if none exists so the reducer's
    # synchronous load → mutate → save cycle has something to read on the
    # first event.
    if not working_memory_repo.exists():
        working_memory_repo.save(WorkingMemory(engagement_id=engagement_id))

    # Episodic repo is required by the reducer constructor — build it
    # here AND mirror it on app.state so ``start_lesson_promoter`` and
    # ``start_l5_summarizer`` can reuse the same instance. Same for the
    # longterm + summary repos: keeping all three together avoids three
    # separate path-derivation passes.
    ep_path = settings.episodic_path
    lt_path = settings.longterm_path
    summary_path = settings.summary_path
    assert (
        ep_path is not None and lt_path is not None and summary_path is not None
    )  # _derive_paths guarantees
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
    """Load ``engagement.yaml`` (if configured) and build ``ScopeEnforcer``.

    Engagement resolution precedence (highest -> lowest):
      1. Env var ``REDROBIN_ENGAGEMENT_SLUG`` (explicit override; CI/tests)
      2. ``~/.redrobin/.active_engagement`` file (operator's persistent
         choice via ``redrobin engagement use <slug>``)
      3. None - degraded wildcard scope, episodic writes skipped

    Without this fallback the daemon ignored the CLI's persistent active
    marker, which silently no-op'd ``engagement use`` for any operator
    not also exporting the env var.

    Stores the loaded engagement and the derived scope on
    ``app.state.engagement`` + ``app.state.scope``. When no slug is
    configured (or the file is missing) we fall back to a wildcard scope
    so the dev workflow without a configured engagement still works.

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

    if engagement is None:
        scope = ScopeEnforcer(in_scope=["*"], out_of_scope=[])
        app.state.engagement = None
    else:
        scope = ScopeEnforcer(
            in_scope=engagement.in_scope, out_of_scope=engagement.out_of_scope
        )
        app.state.engagement = engagement
        # Keep app.state.engagement_id consistent with the loaded slug so the
        # working-memory reducer + downstream consumers see a single ID.
        app.state.engagement_id = engagement.slug

    app.state.scope = scope

    return None, None


async def start_pipeline(
    app: FastAPI, settings: Settings
) -> tuple[Any, CloseHook]:
    """Build the verdict pipeline (Layer A + Layer B) and wire it to the bus.

    Reuses ``app.state.initial_rules`` from ``start_rules`` to avoid a
    second read of the rules directory. Also installs the rules-loader
    hot-reload callback now that Layer A exists — the callback swaps the
    live rule set on the engine in place.

    Wires the engagement summary into Layer B (Slice 7 of Phase 2) when
    an engagement is loaded — guarded by ``hasattr`` so the degraded
    Layer B stub served when API keys are missing silently no-ops.

    Returns the pipeline plus a no-op async close hook (no resources to
    close beyond the event bus which has its own helper).
    """
    initial_rules = app.state.initial_rules
    summary_repo = app.state.summary_repo
    event_bus = app.state.event_bus
    scope = app.state.scope
    engagement = app.state.engagement
    rules_loader = app.state.rules_loader
    engagement_id = app.state.engagement_id
    background_task_factory = app.state.background_task_factory

    # Slice 13 — read the active engagement_id off ``app.state`` each call so
    # a future engagement switch surfaces the right summary on the next
    # verdict without rebuilding Layer B. Today the slug is set once in
    # lifespan, but the indirection is cheap and keeps the API stable.
    def _engagement_id_provider() -> str:
        return getattr(app.state, "engagement_id", engagement_id)

    deps = build_default_dependencies(
        rules=initial_rules,
        summary_repo=summary_repo,
        engagement_id_provider=_engagement_id_provider,
    )
    # Slice 7 — inject the engagement summary into Layer B's cached system
    # prompt so retroactive verdicts incorporate engagement-specific OPSEC
    # posture (low-detection priority, scope nuances, prior tradecraft
    # constraints). Guarded by ``hasattr`` so the degraded Layer B stub —
    # served when API keys are missing — silently no-ops.
    if engagement is not None and hasattr(deps.layer_b, "set_engagement_summary"):
        deps.layer_b.set_engagement_summary(engagement.summary)
    verdict_pipeline = build_pipeline(
        event_bus=event_bus,
        scope=scope,
        layer_a=deps.layer_a,
        layer_b=deps.layer_b,
        task_factory=background_task_factory,
    )
    app.state.verdict_pipeline = verdict_pipeline

    def _on_reload(rules: list) -> None:
        logger.debug("rules_loader: reloaded %d rules", len(rules))
        # Hot-reload Layer A: swap the rule set on the live engine so the
        # singleton pipeline picks up edits without a daemon restart.
        deps.layer_a.update_rules(rules)

    rules_loader.start_hot_reload(_on_reload)

    async def close() -> None:
        return None

    return verdict_pipeline, close


async def start_adapters(
    app: FastAPI, settings: Settings
) -> tuple[dict[str, Any], CloseHook]:
    """Load CS adapter YAML configs and build a logged-in CSRestClient each.

    Slice 9 — closes gap #8: Phase 1 shipped the REST client but the
    daemon never instantiated it. Failures are logged but do not abort
    startup; the daemon is still useful without a live team server.

    Close hook calls ``aclose()`` on every client. Failures during
    shutdown are swallowed — we'd rather get to ``event_bus.close()``
    cleanly than crash on a half-open httpx socket.
    """
    adapters_dir = settings.adapters_dir
    assert adapters_dir is not None  # _derive_paths guarantees
    app.state.cs_adapters = {}
    for cfg in load_adapter_configs(adapters_dir):
        try:
            client = await build_adapter(cfg)
            app.state.cs_adapters[cfg.name] = client
            logger.info("cs_adapter loaded: name=%s url=%s", cfg.name, cfg.teamserver_url)
        except Exception:
            logger.exception("Failed to load CS adapter %s", cfg.name)

    async def close() -> None:
        for name, client in app.state.cs_adapters.items():
            try:
                await client.aclose()
            except Exception:
                logger.exception("error closing cs_adapter %s", name)

    return app.state.cs_adapters, close


async def start_lesson_promoter(
    app: FastAPI, settings: Settings
) -> tuple[LessonPromoter, CloseHook]:
    """Schedule the LessonPromoter loop as a daemon-managed background task.

    Slice 10 — recurring (command, verdict) patterns auto-promote into
    L1 without operator intervention. Interval default of 60 s keeps
    daemon overhead negligible while still surfacing patterns within a
    normal engagement session.

    Close hook cancels the loop and waits up to 2 s for it to exit.
    """
    episodic_repo = app.state.episodic_repo
    longterm_repo = app.state.longterm_repo
    engagement_id = app.state.engagement_id

    promoter_min = settings.promoter_min_occurrences
    promoter = LessonPromoter(
        episodic_repo=episodic_repo,
        longterm_repo=longterm_repo,
        min_occurrences=promoter_min,
    )
    promoter_interval = settings.promoter_interval_sec

    async def _promoter_loop() -> None:
        try:
            while True:
                await asyncio.sleep(promoter_interval)
                try:
                    await promoter.promote(engagement_id)
                except Exception:
                    logger.exception("promoter run failed")
        except asyncio.CancelledError:
            return

    promoter_task = asyncio.create_task(_promoter_loop(), name="lesson_promoter")
    app.state.promoter_task = promoter_task

    async def close() -> None:
        promoter_task.cancel()
        try:
            await asyncio.wait_for(promoter_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    return promoter, close


async def start_l5_summarizer(
    app: FastAPI, settings: Settings
) -> tuple[L5Summarizer, CloseHook]:
    """Schedule the periodic L5 sliding-window summariser.

    Slice 13 — reads recent episodes from the same ``episodic_repo`` the
    reducer writes to, compresses them via the cheap summariser model,
    and appends one row per window to the ``summary_repo`` that Layer B
    reads on every ``evaluate(...)`` call. Schedules next to the
    promoter loop so a single daemon-wide cancel tears both down on
    shutdown.

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


__all__ = [
    "CloseHook",
    "start_adapters",
    "start_engagement",
    "start_event_bus",
    "start_l5_summarizer",
    "start_lesson_promoter",
    "start_pipeline",
    "start_rules",
    "start_working_memory",
]
