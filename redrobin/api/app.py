"""redrobin/api/app.py — FastAPI application factory with lifespan-managed state.

The lifespan handler boots the rules loader at startup, primes the in-memory
rule set, and starts the watchdog hot-reload observer. Shutdown stops the
observer (closes runtime gap #7 — hot reload was implemented but never invoked
at daemon boot).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any, AsyncIterator, Coroutine

from fastapi import Depends, FastAPI, Request

from redrobin.api.adapters_router import router as adapters_router
from redrobin.api.auth import require_bearer
from redrobin.api.events_router import router as events_router
from redrobin.api.tui_ws import router as tui_ws_router
from redrobin.api.verdicts_router import router as verdicts_router
from redrobin.application.config import Settings, validate_for_boot
from redrobin.application.cs_adapter_lifecycle import build_adapter, load_adapter_configs
from redrobin.application.engagement_loader import Engagement, EngagementLoader
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
from redrobin.application.startup_banner import format_banner
from redrobin.application.summarizer import L5Summarizer
from redrobin.application.verdict_pipeline_factory import (
    build_default_dependencies,
    build_pipeline,
)
from redrobin.application.working_memory_lifecycle import WorkingMemory, WorkingMemoryRepo
from redrobin.application.working_memory_reducer import WorkingMemoryReducer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings
    # Slice 3 (v0.1.x hardening): fail-fast on missing secrets unless the
    # operator opted into degraded mode. ``require_root_exists=False`` keeps
    # TestClient happy — pytest fixtures point REDROBIN_ROOT at a tmp_path
    # that may not yet exist when the lifespan runs.
    validate_for_boot(settings)

    rules_dir = settings.rules_dir
    assert rules_dir is not None  # Settings._derive_paths guarantees this
    if "REDROBIN_RULES_DIR" not in os.environ:
        logger.warning(
            "REDROBIN_RULES_DIR not set; defaulting to %s — set explicitly in tests/CI to avoid home pollution.",
            rules_dir,
        )
    rules_dir.mkdir(parents=True, exist_ok=True)

    # Phase 2 condition 4 — daemon-wide tracker for fire-and-forget asyncio
    # tasks. Two production callsites (verdicts_router PASS-dispatch and
    # verdict_pipeline ESCALATE Layer B) used to leak tasks on shutdown.
    # The tracker is initialised before any router or pipeline runs so
    # every callsite can rely on it via ``app.state.background_task_factory``.
    background_tasks: set[asyncio.Task[Any]] = set()
    app.state.background_tasks = background_tasks

    def _track_task(
        coro: Coroutine[Any, Any, Any], *, name: str | None = None
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

    app.state.background_task_factory = _track_task

    rules_loader = RulesLoader(rule_dir=rules_dir)
    initial_rules = rules_loader.load_all()
    app.state.rules_loader = rules_loader

    event_bus = EventBus()
    app.state.event_bus = event_bus

    # Working memory persistence
    wm_path = settings.working_memory_path
    assert wm_path is not None  # Settings._derive_paths guarantees this
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    working_memory_repo = WorkingMemoryRepo(wm_path)
    app.state.working_memory_repo = working_memory_repo

    engagement_id = settings.engagement_id
    app.state.engagement_id = engagement_id

    # Slice 6 — engagement-aware scope. Read engagement.yaml from
    # ``~/.redrobin/engagements/<slug>/`` (root configurable for tests) and
    # build the pipeline's ScopeEnforcer from it. Missing slug falls back to
    # wildcard scope so the dev workflow without a configured engagement
    # still works.
    engagements_dir = settings.engagements_dir
    assert engagements_dir is not None  # Settings._derive_paths guarantees this
    engagement_slug = settings.engagement_slug
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
        engagement_id = engagement.slug

    # Seed an empty working memory file if none exists so the reducer's
    # synchronous load → mutate → save cycle has something to read on the
    # first event.
    if not working_memory_repo.exists():
        working_memory_repo.save(WorkingMemory(engagement_id=engagement_id))

    # Slice 10 — L3 Episodic + L1 Long-term stores. Episodic captures every
    # opsec.verdict event as a raw Episode; LessonPromoter aggregates
    # recurring patterns into structured PromotedLessons stored long-term.
    # Closes gap #15: Phase 1 wired both repos but nothing wrote to them, so
    # L1 retrieval was permanently empty.
    ep_path = settings.episodic_path
    lt_path = settings.longterm_path
    assert ep_path is not None and lt_path is not None  # _derive_paths guarantees
    episodic_repo = EpisodicRepo(ep_path)
    longterm_repo = LongtermRepo(lt_path)
    app.state.episodic_repo = episodic_repo
    app.state.longterm_repo = longterm_repo

    # Slice 13 — L5 sliding-window summary store. Persists periodic
    # compressions of recent episodes so long engagements stay coherent
    # after the most recent verdicts roll off the in-memory window.
    summary_path = settings.summary_path
    assert summary_path is not None  # _derive_paths guarantees
    summary_repo = ContextSummaryRepo(summary_path)
    app.state.summary_repo = summary_repo

    # Reducer subscribes to the bus and mutates working memory + episodic log.
    reducer = WorkingMemoryReducer(
        bus=event_bus,
        repo=working_memory_repo,
        engagement_id=engagement_id,
        episodic_repo=episodic_repo,
    )
    started = asyncio.Event()
    reducer.on_started = started.set  # type: ignore[attr-defined]
    reducer_task = asyncio.create_task(reducer.run(), name="working_memory_reducer")
    await started.wait()  # ensure subscription is active before yielding
    app.state.reducer_task = reducer_task

    # Verdict pipeline — built once at startup with the bus wired so Layer A
    # verdicts publish to the same bus the reducer + TUI WS subscribe to.
    # Reuses the already-loaded rules to avoid double-reading the rules dir.
    # Slice 6: ``scope`` is built from engagement.yaml above (or wildcard
    # fallback). The factory still ships a default scope on ``deps.scope`` —
    # we override it here with the engagement-aware enforcer.
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
        task_factory=_track_task,
    )
    app.state.verdict_pipeline = verdict_pipeline

    def _on_reload(rules: list) -> None:
        logger.debug("rules_loader: reloaded %d rules", len(rules))
        # Hot-reload Layer A: swap the rule set on the live engine so the
        # singleton pipeline picks up edits without a daemon restart.
        deps.layer_a.update_rules(rules)

    rules_loader.start_hot_reload(_on_reload)

    # Slice 9 — load CS adapter YAML configs from REDROBIN_ADAPTERS_DIR and
    # build a logged-in CSRestClient per config. Closes gap #8: Phase 1
    # shipped the REST client but the daemon never instantiated it. Failures
    # are logged but do not abort startup; the daemon is still useful
    # without a live team server (Path 1 alias gate still answers, Path 2
    # dispatch becomes a no-op when the adapter is missing).
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

    # Slice 10 — background task that runs LessonPromoter on a fixed cadence
    # so recurring (command, verdict) patterns auto-promote into L1 without
    # operator intervention. Interval default of 60s keeps daemon overhead
    # negligible while still surfacing patterns within a normal engagement
    # session.
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

    # Slice 13 — periodic L5 sliding-window summariser. Reads recent episodes
    # from the same ``episodic_repo`` the reducer writes to, compresses them
    # via the cheap summariser model, and appends one row per window to the
    # ``summary_repo`` that Layer B reads on every ``evaluate(...)`` call.
    # Schedules next to the promoter loop so a single daemon-wide cancel
    # tears both down on shutdown.
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

    logger.info(
        "daemon lifespan startup complete: rules_dir=%s rules=%d adapters=%d "
        "promoter_interval=%.1fs l5_interval=%.1fs",
        rules_dir,
        len(initial_rules),
        len(app.state.cs_adapters),
        promoter_interval,
        summarizer_interval,
    )

    # Slice 7 (v0.1.x hardening): structured startup banner. Surfaces the
    # resolved config (engagement, rules, adapters, secret key set/missing,
    # L5/promoter tuning) at boot so operators can diagnose "why is it doing
    # X" without diving into logs. Emitted to stderr so the daemon's stdout
    # remains available for structured JSON output. Secret VALUES never
    # appear — only the literal strings ``set`` / ``MISSING (degraded)``.
    try:
        _version = _pkg_version("redrobin")
    except PackageNotFoundError:
        _version = "0.0.0+unknown"
    _banner = format_banner(
        settings=settings,
        rules_count=len(app.state.rules_loader.list_rules()),
        engagement_name=(
            app.state.engagement.name if app.state.engagement is not None else None
        ),
        version=_version,
        adapter_names=list(app.state.cs_adapters.keys()),
    )
    print(_banner, file=sys.stderr, flush=True)

    try:
        yield
    finally:
        # Close adapter REST clients first so any in-flight dispatch sees a
        # live event loop. Failures during shutdown are swallowed — we'd
        # rather get to event_bus.close() cleanly than crash on a half-open
        # httpx socket.
        for name, client in app.state.cs_adapters.items():
            try:
                await client.aclose()
            except Exception:
                logger.exception("error closing cs_adapter %s", name)
        # 1. Drain tracked fire-and-forget tasks (verdicts_router PASS
        #    dispatch + pipeline ESCALATE Layer B). The 5s timeout balances
        #    "give an in-flight LLM call time to finish" against "don't hang
        #    a CTRL+C shutdown". Stragglers are cancelled. This happens
        #    BEFORE closing adapter clients in the previous loop — except we
        #    already closed them; the dispatch tasks are now operating on a
        #    closed httpx session and will fail fast in their own logging
        #    handler, which is fine.
        if background_tasks:
            try:
                done, pending = await asyncio.wait(
                    background_tasks, timeout=5.0
                )
                for task in pending:
                    task.cancel()
            except Exception:
                logger.exception("error draining background_tasks")
        # 2. Cancel the promoter + summariser loops. Both run
        #    independently of the bus and reducer; cancelling them before
        #    ``bus.close()`` avoids a tail promotion/summarisation racing
        #    the shutdown.
        promoter_task.cancel()
        try:
            await asyncio.wait_for(promoter_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        summarizer_task.cancel()
        try:
            await asyncio.wait_for(summarizer_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        # 3. Close the bus — this broadcasts the sentinel to all subscribers
        #    including the reducer. The bus close is what signals reducer to exit
        #    after flushing any final episode write to the episodic DB.
        await event_bus.close()
        # 4. Wait for the reducer to drain and exit cleanly.
        try:
            await asyncio.wait_for(reducer_task, timeout=2.0)
        except asyncio.TimeoutError:
            reducer_task.cancel()
        rules_loader.stop_hot_reload()
        logger.info("daemon lifespan shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Red Robin Daemon", version="0.1.0", lifespan=lifespan)
    app.include_router(adapters_router)
    app.include_router(verdicts_router)
    app.include_router(events_router)
    app.include_router(tui_ws_router)

    @app.get("/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/protected", dependencies=[Depends(require_bearer)])
    async def protected() -> dict[str, str]:
        return {"status": "authenticated"}

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/adapter/heartbeat")
    async def adapter_heartbeat(adapter: str) -> dict[str, str]:
        """Simple liveness probe for the .cna heartbeat loop.

        Unauthenticated and stateless — the .cna fires this every 30s so the
        operator's CS event log warns "[Red Robin] daemon unreachable" the
        moment the daemon dies. Distinct from POST /v1/adapters/heartbeat
        which is session-bound and refreshes a live registry entry.
        """
        return {"status": "ok", "adapter": adapter}

    @app.get("/v1/readyz")
    async def readyz(request: Request) -> dict[str, str]:
        loader = getattr(request.app.state, "rules_loader", None)
        if loader is not None and loader.hot_reload_active:
            return {"status": "ready"}
        return {"status": "not_ready"}

    return app
