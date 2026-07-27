from __future__ import annotations

import json
import logging
import tracemalloc

from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import MAX_TAIL_LINES, ChatHistoryRepo


def _turn(n: int, kind: str = "question", **kw) -> ChatTurn:
    return ChatTurn(
        ts=f"2026-07-24T05:00:{n:02d}.000000+00:00",
        role="operator",
        kind=kind,
        text=f"t{n}",
        **kw,
    )


def test_append_then_tail_round_trips_in_order(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(_turn(1))
    repo.append(_turn(2, kind="advisory", correlation_id="B-7:whoami"))
    turns = repo.tail()
    assert [t.text for t in turns] == ["t1", "t2"]  # oldest first
    assert turns[1].kind == "advisory"
    assert turns[1].correlation_id == "B-7:whoami"
    assert turns[0].correlation_id is None


def test_append_creates_parent_dirs_and_is_one_json_per_line(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "nested" / "dir" / "chat_history.jsonl")
    repo.append(_turn(1))
    repo.append(_turn(2))
    lines = repo.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["text"] == "t1"


def test_missing_file_yields_empty_list(tmp_path):
    # tail does its own missing-file check — there is no exists() to consult.
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    assert repo.tail() == []


def test_malformed_lines_are_skipped_and_neighbours_still_parse(tmp_path):
    path = tmp_path / "chat_history.jsonl"
    good = json.dumps(
        {
            "ts": "2026-07-24T05:00:01.000000+00:00",
            "role": "operator",
            "kind": "question",
            "text": "keep me",
        }
    )
    missing_field = json.dumps({"ts": "x", "role": "operator", "kind": "question"})  # no text
    path.write_text(
        good + "\n" + '{"truncated": ' + "\n" + missing_field + "\n" + "\n" + good + "\n",
        encoding="utf-8",
    )
    repo = ChatHistoryRepo(path)
    turns = repo.tail()
    assert [t.text for t in turns] == ["keep me", "keep me"]  # 2 good, 3 skipped


def test_well_formed_json_that_is_not_an_object_is_skipped_and_logged(tmp_path, caplog):
    # `null` / a bare array / a number all parse as JSON but are not turns. Without a debug
    # line a whole file of them would read as "no history" with nothing to explain it —
    # the other three skip paths all log, so this one must too.
    path = tmp_path / "chat_history.jsonl"
    path.write_text('null\n[1, 2]\n"just a string"\n42\n', encoding="utf-8")
    repo = ChatHistoryRepo(path)
    with caplog.at_level(logging.DEBUG):
        assert repo.tail() == []
    assert "not a JSON object" in caplog.text
    assert caplog.text.count("not a JSON object") == 4  # every skipped line, not just one


def test_tail_honours_max_lines(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    for n in range(1, 11):
        repo.append(_turn(n))
    turns = repo.tail(max_lines=3)
    assert [t.text for t in turns] == ["t8", "t9", "t10"]  # the LAST 3, oldest first


def test_unicode_is_stored_unescaped_and_round_trips(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    text = "¿cómo dumpeo creds? ✻"
    repo.append(
        ChatTurn(ts="2026-07-24T05:00:01.000000+00:00", role="operator", kind="question", text=text)
    )
    # A \uXXXX-escaped file round-trips through json.loads identically, so parsing the turn
    # back proves NOTHING about ensure_ascii=False. Assert on the bytes on disk: the
    # transcript is meant to stay grep-able by the operator, not escaped.
    raw = repo.path.read_text(encoding="utf-8")
    assert text in raw
    assert "\\u" not in raw
    assert repo.tail()[0].text == text


def test_tail_is_bounded_by_the_default_cap(tmp_path):
    # Behavioural guard for the DEFAULT cap: every other tail test passes an explicit
    # max_lines, so nothing caught a default of None / 0 / "unbounded". Subsumes the old
    # literal pin of the constant. Written directly (no per-turn append) to stay cheap.
    assert MAX_TAIL_LINES == 2000  # far above the two context quotas (10 + 5)
    path = tmp_path / "chat_history.jsonl"
    lines = [
        json.dumps(
            {
                "ts": f"2026-07-24T05:00:00.{n:06d}+00:00",
                "role": "operator",
                "kind": "question",
                "text": f"t{n}",
            }
        )
        for n in range(MAX_TAIL_LINES + 100)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    turns = ChatHistoryRepo(path).tail()
    assert len(turns) == MAX_TAIL_LINES
    assert turns[0].text == "t100"  # the oldest 100 fell outside the window
    assert turns[-1].text == f"t{MAX_TAIL_LINES + 99}"


def test_tail_does_not_load_the_whole_file(tmp_path):
    # The bounded-read PROPERTY, not just the bounded result. A
    # ``read_text().splitlines()[-n:]`` rewrite returns byte-identical turns and would pass
    # every other test in this file while allocating the entire transcript — the deque is
    # there precisely so memory is O(max_lines), never O(file). Measure the allocation.
    path = tmp_path / "chat_history.jsonl"
    row = json.dumps(
        {
            "ts": "2026-07-24T05:00:01.000000+00:00",
            "role": "assistant",
            "kind": "advisory",
            "text": "x" * 60,
            "correlation_id": None,
        }
    )
    path.write_text("\n".join([row] * 10_000) + "\n", encoding="utf-8")
    file_bytes = path.stat().st_size
    assert file_bytes > 1_000_000  # the file must dwarf the window for this to mean anything

    was_tracing = tracemalloc.is_tracing()
    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        turns = ChatHistoryRepo(path).tail(max_lines=50)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        if not was_tracing:
            tracemalloc.stop()

    assert len(turns) == 50
    # Measured 2026-07-27: ~46 KB for the deque read vs ~10 MB for the read_text() rewrite.
    # A tenth of the file is a deliberately loose ceiling that the naive version still fails.
    assert peak < file_bytes / 10
