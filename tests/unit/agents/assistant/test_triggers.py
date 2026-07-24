from redrobin.application.agents.assistant.triggers import TriggerGate


def _gate(now_ref):
    return TriggerGate(cooldown_sec=30.0, clock=lambda: now_ref[0])


def test_speaks_on_first_signal():
    gate = _gate([100.0])
    assert gate.should_speak(correlation_id="c1", state_signature="s1") is True


def test_dedup_same_correlation_is_silent():
    now = [100.0]
    gate = _gate(now)
    gate.record_spoken(correlation_id="c1", state_signature="s1")
    now[0] = 200.0  # well past the cooldown
    assert gate.should_speak(correlation_id="c1", state_signature="s2") is False


def test_novelty_unchanged_state_is_silent():
    now = [100.0]
    gate = _gate(now)
    gate.record_spoken(correlation_id="c1", state_signature="s1")
    now[0] = 200.0
    assert gate.should_speak(correlation_id="c2", state_signature="s1") is False  # same sig


def test_cooldown_blocks_then_allows():
    now = [100.0]
    gate = _gate(now)
    gate.record_spoken(correlation_id="c1", state_signature="s1")
    now[0] = 110.0  # within the 30s cooldown, new state, new corr
    assert gate.should_speak(correlation_id="c2", state_signature="s2") is False
    now[0] = 135.0  # past the cooldown
    assert gate.should_speak(correlation_id="c2", state_signature="s2") is True


def test_silent_cycle_does_not_burn_cooldown_or_dedup():
    # should_speak is a pure check; only record_spoken commits. A cycle that stays
    # silent (never records) must not block the next signal.
    now = [100.0]
    gate = _gate(now)
    assert gate.should_speak(correlation_id="c1", state_signature="s1") is True
    # no record_spoken -> the next distinct signal still speaks
    assert gate.should_speak(correlation_id="c2", state_signature="s2") is True


def test_corr_cache_evicts_oldest_beyond_max():
    # The dedup cache is bounded (FIFO) so a long engagement can't grow it
    # unbounded. Record 257 distinct correlations (one past _CORR_CACHE_MAX=256):
    # the oldest is evicted, so should_speak no longer dedups it.
    now = [100.0]
    gate = TriggerGate(cooldown_sec=0.0, clock=lambda: now[0])
    for i in range(257):
        gate.record_spoken(correlation_id=f"c{i}", state_signature=f"s{i}")
    assert len(gate._seen_corr) == 256
    now[0] = 1000.0  # well past any cooldown
    # the oldest correlation (c0) was evicted -> a fresh, novel signal speaks again
    assert gate.should_speak(correlation_id="c0", state_signature="s-new") is True
    # a still-resident recent correlation is still deduped
    assert gate.should_speak(correlation_id="c256", state_signature="s-new2") is False
