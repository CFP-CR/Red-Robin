from __future__ import annotations

import json
import logging

from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo


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


def test_missing_file_yields_empty_list_and_exists_false(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    assert repo.exists() is False
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


def test_unicode_round_trips(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(
        ChatTurn(
            ts="2026-07-24T05:00:01.000000+00:00",
            role="operator",
            kind="question",
            text="¿cómo dumpeo creds? ✻",
        )
    )
    assert repo.tail()[0].text == "¿cómo dumpeo creds? ✻"


def test_default_max_tail_lines_is_2000():
    # The cap bounds the read AND, through it, the selection — so it sits far above the two
    # context quotas (10 + 5) to keep a saturated tail from starving a window.
    from redrobin.infrastructure.persistence.chat_history_repo import MAX_TAIL_LINES

    assert MAX_TAIL_LINES == 2000
