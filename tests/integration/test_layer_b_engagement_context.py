"""Slice 7 integration: Layer B prompt includes engagement summary.

Closes Phase 2 gap #5: ``LayerBEngine.set_engagement_summary`` exists but is
never called at daemon boot, so the engagement summary surfaced by
:class:`~redrobin.application.engagement_loader.EngagementLoader` (Slice 6)
never reaches Layer B's LLM prompt.

This slice wires:
  1. The summary string into a **cached** system block on every Layer B call
     (5min TTL, separate from the per-turn uncached dynamic state). The block
     amortizes the engagement text across all retroactive verdicts within a
     session.
  2. The lifespan handler in ``redrobin.api.app`` to call
     ``layer_b.set_engagement_summary(engagement.summary)`` immediately after
     the engagement.yaml is loaded.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from redrobin.application.kb_service import KBService
from redrobin.application.layer_b_engine import LayerBEngine
from redrobin.domain.llm import LLMResponse


def _mock_llm(text: str) -> AsyncMock:
    llm = AsyncMock()
    llm.complete_with_cache = AsyncMock(
        return_value=LLMResponse(
            text=text,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=10,
        )
    )
    return llm


def _mock_kb(hits=None) -> AsyncMock:
    kb = AsyncMock(spec=KBService)
    kb.retrieve = AsyncMock(return_value=hits or [])
    return kb


@pytest.mark.asyncio
async def test_layer_b_includes_engagement_summary_in_cached_system_block():
    """Verify ``set_engagement_summary`` populates a cached system block.

    The summary must travel on the ``cacheable_engagement_summary`` keyword so
    the Anthropic client wraps it in a ``cache_control: ephemeral 5min`` block
    (stable across all retroactive verdicts within a session) rather than on
    the per-turn uncached ``dynamic_state`` keyword.
    """
    llm = _mock_llm('```json\n{"action":"PASS","reason":"ok"}\n```')
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)
    engine.set_engagement_summary(
        "Engagement: op-redspark — low-detection priority, EU corp."
    )

    await engine.evaluate(command="mimikatz", raw="mimikatz")

    call = llm.complete_with_cache.await_args
    cached_summary = call.kwargs["cacheable_engagement_summary"]
    assert "op-redspark" in cached_summary
    assert "low-detection priority" in cached_summary


@pytest.mark.asyncio
async def test_layer_b_omits_summary_block_when_unset():
    """No ``set_engagement_summary`` call → empty cacheable block (no leakage)."""
    llm = _mock_llm('```json\n{"action":"PASS","reason":"ok"}\n```')
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)

    await engine.evaluate(command="whoami", raw="whoami")

    call = llm.complete_with_cache.await_args
    assert call.kwargs["cacheable_engagement_summary"] == ""


@pytest.mark.asyncio
async def test_lifespan_sets_engagement_summary_on_layer_b(tmp_path, monkeypatch):
    """When engagement.yaml is loaded, lifespan calls ``set_engagement_summary``.

    Sets stub ANTHROPIC_API_KEY + VOYAGE_API_KEY so the factory builds the real
    :class:`LayerBEngine` (the SDK clients are constructed but never invoked —
    the test stops before any HTTP call).
    """
    eng_dir = tmp_path / "engagements" / "op-ctx"
    eng_dir.mkdir(parents=True)
    (eng_dir / "engagement.yaml").write_text(
        "slug: op-ctx\n"
        "name: Context Test\n"
        "in_scope:\n  - corp.local\n"
        "out_of_scope: []\n"
        "summary: |\n  Low-detection priority. EU corp.\n"
    )
    monkeypatch.setenv("REDROBIN_ENGAGEMENTS_DIR", str(tmp_path / "engagements"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "op-ctx")
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv(
        "REDROBIN_WORKING_MEMORY_PATH",
        str(tmp_path / "working_memory.json"),
    )
    # Stub keys so the factory builds the real :class:`LayerBEngine` rather
    # than the degraded stub. SDK client construction does not perform a
    # network call, and this test never invokes ``evaluate()``.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test-only")

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        layer_b = app.state.verdict_pipeline._layer_b
        assert isinstance(layer_b, LayerBEngine), (
            "Expected real LayerBEngine — got degraded stub. Check API key env."
        )
        assert "Low-detection priority" in layer_b._engagement_summary
        assert "EU corp." in layer_b._engagement_summary
