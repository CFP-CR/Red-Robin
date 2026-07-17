"""Slice 13 integration: L5 sliding-window summary.

Closes Phase 2 gap #18 — Phase 1 spec called for an L5 sliding-window summary
that compresses recent verdicts so long engagements stay coherent after the
most recent rows roll off the in-memory window. Phase 1 shipped neither the
persistence row, the summarizer task, nor the prompt injection.

This slice wires the full chain:

1. ``ContextSummaryRepo`` persists one row per compression event (sync
   sqlite3, lazy schema — same pattern as the Slice 10 ``EpisodicRepo`` /
   ``LongtermRepo``).
2. ``L5Summarizer`` reads recent episodes from ``EpisodicRepo`` and, when the
   episode count crosses ``window_size``, asks the LLM for a 3-5 sentence
   compression, then writes the result into the summary repo.
3. ``LayerBEngine`` consumes the latest summary on every ``evaluate(...)``
   call and forwards it to ``LLMProvider.complete_with_cache`` as the new
   ``cacheable_recent_summary`` keyword. The Anthropic client emits a
   separate ephemeral cached block (5min TTL) so cache invalidation for the
   engagement summary (rare) is independent from the recent summary
   (every ~25 verdicts).
4. The daemon lifespan schedules a periodic summarizer task next to the
   existing ``LessonPromoter`` loop, and rebuilds Layer B with the new repo
   + engagement_id provider so the cached block is always populated from
   the active engagement.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from redrobin.application.layer_b_engine import LayerBEngine
from redrobin.application.agents.summarizer import L5Summarizer
from redrobin.domain.memory import ContextSummary
from redrobin.infrastructure.persistence.context_summary_repo import (
    ContextSummaryRepo,
)
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo


@pytest.mark.asyncio
async def test_summarizer_emits_summary_after_window(tmp_path: Path) -> None:
    """Window threshold met → summarizer compresses + persists one summary row."""
    ep = EpisodicRepo(tmp_path / "ep.db")
    cs = ContextSummaryRepo(tmp_path / "cs.db")

    class FakeLLM:
        async def summarize(self, *, episodes_text: str) -> str:
            assert "cmd-0" in episodes_text and "cmd-4" in episodes_text
            return "Window summary: 5 episodes compressed."

    summarizer = L5Summarizer(
        episodic_repo=ep,
        summary_repo=cs,
        llm=FakeLLM(),
        engagement_id="eng-test",
        window_size=5,
    )

    for i in range(5):
        ep.record(
            engagement_id="eng-test",
            correlation_id=f"c-{i}",
            command=f"cmd-{i}",
            verdict="ALLOW",
            reason="",
        )

    emitted = await summarizer.run_once()
    assert emitted is True
    summaries = cs.list_recent("eng-test", limit=5)
    assert len(summaries) == 1
    assert "Window summary" in summaries[0].text


@pytest.mark.asyncio
async def test_summarizer_skip_when_below_window(tmp_path: Path) -> None:
    """Episode count below threshold → no LLM call, no summary row."""
    ep = EpisodicRepo(tmp_path / "ep.db")
    cs = ContextSummaryRepo(tmp_path / "cs.db")

    class _FailLLM:
        async def summarize(self, *, episodes_text: str) -> str:  # pragma: no cover
            raise AssertionError("LLM must not be called below window")

    summarizer = L5Summarizer(
        episodic_repo=ep,
        summary_repo=cs,
        llm=_FailLLM(),
        engagement_id="eng",
        window_size=10,
    )
    for i in range(3):
        ep.record(
            engagement_id="eng",
            correlation_id=f"c-{i}",
            command="x",
            verdict="ALLOW",
            reason="",
        )

    emitted = await summarizer.run_once()
    assert emitted is False
    assert cs.list_recent("eng", limit=5) == []


@pytest.mark.asyncio
async def test_summarizer_only_summarizes_once_per_window(tmp_path: Path) -> None:
    """Calling ``run_once`` twice on the same window must not duplicate rows.

    The summarizer tracks the high-water mark of summarised episodes so a
    second pass with no new episodes is a no-op. New episodes appended after
    the threshold passes trigger a new summary only when the next window
    boundary is crossed.
    """
    ep = EpisodicRepo(tmp_path / "ep.db")
    cs = ContextSummaryRepo(tmp_path / "cs.db")

    class FakeLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def summarize(self, *, episodes_text: str) -> str:
            self.calls += 1
            return f"summary-{self.calls}"

    llm = FakeLLM()
    summarizer = L5Summarizer(
        episodic_repo=ep,
        summary_repo=cs,
        llm=llm,
        engagement_id="eng-idem",
        window_size=3,
    )
    for i in range(3):
        ep.record(
            engagement_id="eng-idem",
            correlation_id=f"c-{i}",
            command="cmd",
            verdict="ALLOW",
            reason="",
        )

    assert await summarizer.run_once() is True
    assert await summarizer.run_once() is False  # no new episodes
    assert llm.calls == 1
    assert len(cs.list_recent("eng-idem", limit=5)) == 1


@pytest.mark.asyncio
async def test_layer_b_includes_latest_summary_in_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Layer B reads latest summary from repo and forwards it as a cached block."""
    cs = ContextSummaryRepo(tmp_path / "cs.db")
    cs.append(
        ContextSummary(
            engagement_id="eng-x",
            text="Three BLOCKs on mimikatz this session.",
            window_size=5,
        )
    )

    captured: dict = {}

    class FakeLLM:
        async def complete_with_cache(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            from redrobin.domain.llm import LLMResponse

            return LLMResponse(
                text='```json\n{"action":"PASS","reason":"ok","suggestions":[]}\n```',
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                output_tokens=5,
            )

    from unittest.mock import AsyncMock

    from redrobin.application.kb_service import KBService

    kb = AsyncMock(spec=KBService)
    kb.retrieve = AsyncMock(return_value=[])

    engine = LayerBEngine(
        llm=FakeLLM(),
        kb=kb,
        summary_repo=cs,
        engagement_id_provider=lambda: "eng-x",
    )
    await engine.evaluate(command="whoami", raw="whoami")

    cached = captured.get("cacheable_recent_summary", "")
    assert "Three BLOCKs on mimikatz" in cached


@pytest.mark.asyncio
async def test_layer_b_omits_recent_summary_block_when_no_summary(
    tmp_path: Path,
) -> None:
    """No summary persisted → ``cacheable_recent_summary`` is the empty string."""
    cs = ContextSummaryRepo(tmp_path / "cs.db")

    captured: dict = {}

    class FakeLLM:
        async def complete_with_cache(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            from redrobin.domain.llm import LLMResponse

            return LLMResponse(
                text='```json\n{"action":"PASS","reason":"ok","suggestions":[]}\n```',
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                output_tokens=5,
            )

    from unittest.mock import AsyncMock

    from redrobin.application.kb_service import KBService

    kb = AsyncMock(spec=KBService)
    kb.retrieve = AsyncMock(return_value=[])

    engine = LayerBEngine(
        llm=FakeLLM(),
        kb=kb,
        summary_repo=cs,
        engagement_id_provider=lambda: "eng-empty",
    )
    await engine.evaluate(command="whoami", raw="whoami")
    assert captured.get("cacheable_recent_summary", "") == ""


@pytest.mark.asyncio
async def test_layer_b_works_without_summary_repo(tmp_path: Path) -> None:
    """Legacy construction (no repo) must still pass an empty recent-summary block."""
    captured: dict = {}

    class FakeLLM:
        async def complete_with_cache(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            from redrobin.domain.llm import LLMResponse

            return LLMResponse(
                text='```json\n{"action":"PASS","reason":"ok","suggestions":[]}\n```',
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                output_tokens=5,
            )

    from unittest.mock import AsyncMock

    from redrobin.application.kb_service import KBService

    kb = AsyncMock(spec=KBService)
    kb.retrieve = AsyncMock(return_value=[])

    engine = LayerBEngine(llm=FakeLLM(), kb=kb)
    await engine.evaluate(command="whoami", raw="whoami")
    assert captured.get("cacheable_recent_summary", "") == ""


@pytest.mark.asyncio
async def test_lifespan_wires_summarizer_and_layer_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lifespan boots summarizer + threads the summary repo into Layer B.

    Stub keys so the factory builds the real :class:`LayerBEngine` (SDK
    construction does not hit the network). Episode records seed the
    summariser; we verify the periodic task fires within one interval and
    Layer B is wired to the same repo.
    """
    # Isolate from any active engagement on the dev machine's real ~/.redrobin/
    # so EngagementService.current() returns None and the env-var-set
    # REDROBIN_ENGAGEMENT_ID below wins via the degraded fallback path.
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv(
        "REDROBIN_WORKING_MEMORY_PATH",
        str(tmp_path / "working_memory.json"),
    )
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_L5_WINDOW", "3")
    monkeypatch.setenv("REDROBIN_L5_INTERVAL_SEC", "0.05")
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-l5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test-only")

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        # Repo is on app.state and Layer B reads from the same repo.
        assert app.state.summary_repo is not None
        layer_b = app.state.verdict_pipeline._layer_b
        assert isinstance(layer_b, LayerBEngine)
        assert layer_b._summary_repo is app.state.summary_repo

        # Stub LLM so the periodic summarizer does not hit the network.
        class FakeLLM:
            async def summarize(self, *, episodes_text: str) -> str:
                return "lifespan summary"

        app.state.summarizer._llm = FakeLLM()  # type: ignore[attr-defined]

        ep = app.state.episodic_repo
        for i in range(3):
            ep.record(
                engagement_id="eng-l5",
                correlation_id=f"c-{i}",
                command="x",
                verdict="ALLOW",
                reason="",
            )
        # Drive the loop until the periodic task picks up the episodes.
        summaries: list = []
        for _ in range(80):
            await asyncio.sleep(0.05)
            summaries = app.state.summary_repo.list_recent("eng-l5", limit=5)
            if summaries:
                break
        assert summaries, "summarizer task never produced a summary"
        assert "lifespan summary" in summaries[0].text
