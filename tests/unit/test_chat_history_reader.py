from __future__ import annotations

import json
import logging

from redrobin.application.chat.history import _MAX_TURN_CHARS, build_history_reader, render_turns
from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import (
    MAX_TAIL_LINES,
    ChatHistoryRepo,
)


def _turn(n: int, kind: str, role: str, text: str) -> ChatTurn:
    return ChatTurn(ts=f"2026-07-24T05:00:{n:02d}.000000+00:00", role=role, kind=kind, text=text)


def test_render_labels_roles_and_marks_volunteered_kinds():
    out = render_turns(
        [
            _turn(1, "question", "operator", "path to DA?"),
            _turn(2, "answer", "assistant", "Kerberoast svc_sql"),
            _turn(3, "advisory", "assistant", "Validate svc_reports"),
            _turn(4, "heads_up", "assistant", "Baseline defences"),
        ]
    )
    assert "[operator] path to DA?" in out
    assert "[assistant] Kerberoast svc_sql" in out
    assert "[assistant · advisory] Validate svc_reports" in out
    assert "[assistant · heads-up] Baseline defences" in out


def test_render_empty_is_empty_string():
    assert render_turns([]) == ""


def test_render_truncates_a_long_turn_and_leaves_a_short_one_alone():
    # The selector bounds turn COUNT only; one turn can be an 8 KB answer or an arbitrary
    # paste, so the byte size of the injected slice has to be bounded per turn too.
    long_text = "A" * (_MAX_TURN_CHARS + 500)
    out = render_turns([_turn(1, "question", "operator", long_text)])
    assert "…[truncated]" in out
    assert out.count("A") == _MAX_TURN_CHARS
    assert len(out) < len(long_text)

    short = "B" * _MAX_TURN_CHARS  # exactly at the cap — untouched
    out = render_turns([_turn(2, "answer", "assistant", short)])
    assert out == f"[assistant] {short}"
    assert "…[truncated]" not in out


def test_reader_returns_rendered_selection(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(_turn(1, "question", "operator", "path to DA?"))
    repo.append(_turn(2, "advisory", "assistant", "Validate svc_reports"))
    read = build_history_reader(repo, dialogue_max=10, volunteered_max=5)
    out = read()
    assert "[operator] path to DA?" in out
    assert "[assistant · advisory] Validate svc_reports" in out


def test_reader_applies_the_two_windows(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(_turn(1, "question", "operator", "old question"))
    for n in range(2, 12):
        repo.append(_turn(n, "advisory", "assistant", f"adv{n}"))
    read = build_history_reader(repo, dialogue_max=10, volunteered_max=2)
    out = read()
    assert "old question" in out  # dialogue quota protected it
    # Only the last 2 advisories. Count "] adv" (the rendered text) rather than "adv":
    # the volunteered label itself contains that substring ("[assistant · advisory]").
    assert out.count("] adv") == 2
    assert "adv11" in out  # the newest survived
    assert "adv9" not in out  # an older one did not


def test_reader_returns_empty_string_when_there_is_no_history(tmp_path):
    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    assert build_history_reader(repo, dialogue_max=10, volunteered_max=5)() == ""


def test_reader_logs_a_debug_line_when_the_tail_is_saturated(tmp_path, caplog):
    # The read cap bounds the SELECTION too: a tail made entirely of volunteered turns
    # starves the dialogue quota even though dialogue exists earlier in the file. Rare, but
    # silent — so it must at least be diagnosable. Written directly (no per-turn append) to
    # keep the test cheap.
    path = tmp_path / "chat_history.jsonl"
    lines = [
        json.dumps(
            {
                "ts": "2026-07-24T05:00:00.000000+00:00",
                "role": "operator",
                "kind": "question",
                "text": "SECRET-PASSWORD",
            }
        )
    ]
    lines += [
        json.dumps(
            {
                "ts": f"2026-07-24T06:00:00.{n:06d}+00:00",
                "role": "assistant",
                "kind": "advisory",
                "text": f"adv{n}",
            }
        )
        for n in range(MAX_TAIL_LINES)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    read = build_history_reader(ChatHistoryRepo(path), dialogue_max=10, volunteered_max=2)
    with caplog.at_level(logging.DEBUG):
        out = read()
    assert "SECRET-PASSWORD" not in out  # the old question fell outside the read window
    assert "tail cap hit" in caplog.text
    assert "SECRET-PASSWORD" not in caplog.text  # counts only — never a turn's text


def test_reader_returns_empty_string_when_the_repo_raises(tmp_path):
    class _BoomRepo:
        path = tmp_path / "chat_history.jsonl"

        def tail(self, max_lines: int = MAX_TAIL_LINES):
            raise OSError("unreadable")

    read = build_history_reader(_BoomRepo(), dialogue_max=10, volunteered_max=5)  # type: ignore[arg-type]
    assert read() == ""  # degrades, never raises
