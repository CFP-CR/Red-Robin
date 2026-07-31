from __future__ import annotations

import dataclasses

import pytest

from redrobin.domain.chat import (
    DIALOGUE_KINDS,
    VOLUNTEERED_KINDS,
    ChatTurn,
    select_context_turns,
)


def _turn(n: int, kind: str) -> ChatTurn:
    # ts sorts lexicographically the same as chronologically (zero-padded)
    role = "operator" if kind == "question" else "assistant"
    return ChatTurn(ts=f"2026-07-24T05:00:{n:02d}.000000+00:00", role=role, kind=kind, text=f"t{n}")


def test_kind_groups_are_disjoint_and_complete():
    assert DIALOGUE_KINDS == frozenset({"question", "answer", "confirm"})
    assert VOLUNTEERED_KINDS == frozenset({"advisory", "heads_up"})
    assert not (DIALOGUE_KINDS & VOLUNTEERED_KINDS)


def test_each_group_gets_its_own_quota():
    turns = [_turn(1, "question"), _turn(2, "answer"), _turn(3, "advisory"), _turn(4, "heads_up")]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=10)
    assert [t.text for t in out] == ["t1", "t2", "t3", "t4"]


def test_advisory_burst_cannot_evict_the_dialogue_thread():
    # THE dilution guarantee (spec §6): 20 advisories after a question must NOT
    # push that question out of the selected window.
    turns = [_turn(1, "question"), _turn(2, "answer")]
    turns += [_turn(i, "advisory") for i in range(3, 23)]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=5)
    texts = [t.text for t in out]
    assert "t1" in texts and "t2" in texts  # dialogue survived
    assert len([t for t in out if t.kind == "advisory"]) == 5  # advisory quota honoured
    assert texts[:2] == ["t1", "t2"]  # and the merge is chronological


def test_windows_keep_the_most_recent_of_each_group():
    turns = [_turn(i, "question") for i in range(1, 6)] + [
        _turn(i, "advisory") for i in range(6, 11)
    ]
    out = select_context_turns(turns, dialogue_max=2, volunteered_max=2)
    assert [t.text for t in out] == ["t4", "t5", "t9", "t10"]


def test_merge_is_chronological_across_groups():
    turns = [_turn(1, "advisory"), _turn(2, "question"), _turn(3, "advisory"), _turn(4, "answer")]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=10)
    assert [t.text for t in out] == ["t1", "t2", "t3", "t4"]


def test_fewer_turns_than_the_caps_returns_all():
    turns = [_turn(1, "question"), _turn(2, "advisory")]
    assert len(select_context_turns(turns, dialogue_max=10, volunteered_max=10)) == 2


def test_empty_input_returns_empty():
    assert select_context_turns([], dialogue_max=10, volunteered_max=5) == []


def test_unknown_kind_is_ignored():
    turns = [
        _turn(1, "question"),
        ChatTurn(
            ts="2026-07-24T05:00:02.000000+00:00", role="assistant", kind="telemetry", text="leak"
        ),
    ]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=10)
    assert [t.text for t in out] == ["t1"]


def test_zero_cap_selects_nothing_from_that_group():
    # a 0 cap must select NOTHING from the group — a naive turns[-0:] would select ALL
    turns = [_turn(1, "question"), _turn(2, "advisory")]
    out = select_context_turns(turns, dialogue_max=0, volunteered_max=10)
    assert [t.text for t in out] == ["t2"]
    out = select_context_turns(turns, dialogue_max=10, volunteered_max=0)
    assert [t.text for t in out] == ["t1"]


def test_chat_turn_is_frozen_and_defaults_correlation_id():
    t = _turn(1, "question")
    assert t.correlation_id is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.text = "mutated"  # type: ignore[misc]
