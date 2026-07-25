from __future__ import annotations

from redrobin.application.chat.history import build_history_reader, render_turns
from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo


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


def test_reader_returns_empty_string_when_the_repo_raises(tmp_path):
    class _BoomRepo:
        path = tmp_path / "chat_history.jsonl"

        def tail(self, max_lines: int = 500):
            raise OSError("unreadable")

    read = build_history_reader(_BoomRepo(), dialogue_max=10, volunteered_max=5)  # type: ignore[arg-type]
    assert read() == ""  # degrades, never raises
