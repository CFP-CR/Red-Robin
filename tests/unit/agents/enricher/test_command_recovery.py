"""Unit tests for the enricher's command-recovery cache (correlation_id → raw
command, with a per-bid last-command fallback)."""

from __future__ import annotations

from redrobin.application.agents.enricher.enricher import WorkingMemoryEnricher
from redrobin.application.event_bus import EventBus


def _make_enricher() -> WorkingMemoryEnricher:
    return WorkingMemoryEnricher(
        bus=EventBus(),
        llm=None,
        denylist=["ls"],
        min_output_chars=8,
        chunk_max_chars=16000,
        enabled=True,
    )


def test_exact_match_by_correlation_id():
    e = _make_enricher()
    e._remember_command({"bid": "B-1", "raw": "shell systeminfo", "correlation_id": "18ff"})
    assert (
        e._recover_command({"bid": "B-1", "output": "x", "correlation_id": "18ff"})
        == "shell systeminfo"
    )


def test_correlation_id_is_evicted_on_match():
    e = _make_enricher()
    e._remember_command({"bid": "B-1", "raw": "shell whoami", "correlation_id": "abcd"})
    assert e._recover_command({"bid": "B-1", "correlation_id": "abcd"}) == "shell whoami"
    # Second lookup of the same correlation id no longer hits the corr cache; it
    # falls back to the per-bid last command (which is still "shell whoami").
    assert e._recover_command({"bid": "B-1", "correlation_id": "abcd"}) == "shell whoami"


def test_falls_back_to_bid_last_command_when_correlation_absent():
    e = _make_enricher()
    e._remember_command({"bid": "B-2", "raw": "shell ipconfig", "correlation_id": "9090"})
    # output with no correlation_id at all → per-bid fallback
    assert e._recover_command({"bid": "B-2", "output": "x"}) == "shell ipconfig"


def test_falls_back_to_bid_when_correlation_unknown():
    e = _make_enricher()
    e._remember_command({"bid": "B-3", "raw": "shell hostname", "correlation_id": "1111"})
    # a correlation_id we never saw → not in corr cache → per-bid fallback
    assert e._recover_command({"bid": "B-3", "correlation_id": "ffff"}) == "shell hostname"


def test_degraded_returns_empty_when_nothing_known():
    e = _make_enricher()
    assert e._recover_command({"bid": "B-9", "output": "x"}) == ""


def test_remember_prefers_raw_then_command_then_empty():
    e = _make_enricher()
    e._remember_command({"bid": "B-4", "command": "shell", "correlation_id": "aa"})
    assert e._recover_command({"bid": "B-4", "correlation_id": "aa"}) == "shell"
    e._remember_command({"bid": "B-5", "correlation_id": "bb"})  # neither raw nor command
    assert e._recover_command({"bid": "B-5", "correlation_id": "bb"}) == ""


def test_corr_cache_is_bounded_evicts_oldest():
    e = _make_enricher()
    cap = e._CORR_CACHE_MAX
    for i in range(cap + 5):
        e._remember_command({"bid": "B", "raw": f"cmd{i}", "correlation_id": f"c{i}"})
    # the corr cache never exceeds the cap
    assert len(e._cmd_by_corr) <= cap
    # the 5 oldest correlation ids were evicted (FIFO); their lookup misses the
    # corr cache and falls through to the per-bid last command ("cmd{cap+4}")
    assert e._recover_command({"bid": "B", "correlation_id": "c0"}) == f"cmd{cap + 4}"
    # a recent correlation id still resolves exactly
    assert e._recover_command({"bid": "B", "correlation_id": f"c{cap + 4}"}) == f"cmd{cap + 4}"
