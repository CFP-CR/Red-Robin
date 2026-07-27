"""TUI WebSocket bridge — auth + initial snapshot.

The echo loop tests were removed in Slice 3: the endpoint no longer echoes
operator input. Bus-driven broadcasting is covered by
``test_tui_ws_broadcast.py``.
"""

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    # Pin the assistant OFF rather than inheriting it. ``redrobin.cli._load_env_for_cli``
    # calls ``load_dotenv`` on os.environ, so once a CLI test has run, a dev box whose
    # .env sets REDROBIN_ASSISTANT_ENABLED leaks it into every later test in the session —
    # and the lifespan would then leave a chat_history_repo on app.state. These tests own
    # what sits on app.state, so the boot state must not depend on suite order or on a
    # local .env.
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "0")
    yield root


@pytest.fixture
def client(_isolated_root):
    # Use the ``with`` context so FastAPI's lifespan runs and app.state.event_bus
    # is populated — Slice 3 onwards the WS endpoint reads it on accept.
    with TestClient(create_app()) as client:
        yield client


def _receive_json(ws, timeout: float = 5.0) -> Any:
    """A BOUNDED ``ws.receive_json()``.

    starlette's TestClient receives through a portal with no timeout, and after the app
    returns its ``_run`` sleeps forever, so the stream never closes either. Without this,
    a missing frame wedges the whole pytest session rather than failing a test — which
    would make the RED step of this task unrunnable. ``pytest-timeout`` is not installed.
    """
    box: dict[str, Any] = {}

    def _pull() -> None:
        try:
            box["frame"] = ws.receive_json()
        except Exception as exc:  # the portal can be torn down under us on timeout
            box["error"] = exc

    thread = threading.Thread(target=_pull, daemon=True)
    thread.start()
    thread.join(timeout)
    if "frame" not in box:
        raise AssertionError(f"no frame within {timeout}s (last error: {box.get('error')!r})")
    return box["frame"]


def test_ws_rejects_wrong_token(client):
    try:
        with client.websocket_connect("/v1/tui?token=wrong"):
            pass
    except Exception:
        # Expected: connection rejected
        return
    pytest.fail("expected websocket to be rejected")


def test_ws_accepts_correct_token_sends_initial_snapshot(client, _isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "working_memory_snapshot"


def test_ws_sends_chat_history_after_the_snapshot(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    # the assistant is disabled in this fixture, so the lifespan sets no repo: the empty
    # case is the COMMON path, not an edge case
    assert not hasattr(client.app.state, "chat_history_repo")
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = _receive_json(ws)
        second = _receive_json(ws)
    assert first["type"] == "working_memory_snapshot"
    assert second["type"] == "chat_history"
    assert second["payload"]["turns"] == []


def test_ws_chat_history_carries_the_full_turn_shape(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(
        ChatTurn(ts="2026-07-27T00:00:00+00:00", role="operator", kind="question", text="hello")
    )
    repo.append(
        ChatTurn(
            ts="2026-07-27T00:00:01+00:00",
            role="assistant",
            kind="advisory",
            text="enumerate the subnet",
            correlation_id="B-1:whoami",
        )
    )
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)  # working_memory_snapshot
        frame = _receive_json(ws)
    turns = frame["payload"]["turns"]
    assert [t["text"] for t in turns] == ["hello", "enumerate the subnet"]  # oldest first
    # the frame carries the whole ChatTurn, not a subset (spec: the frame contract)
    assert set(turns[0]) == {"ts", "role", "kind", "text", "correlation_id"}
    assert turns[0]["correlation_id"] is None
    assert turns[1]["correlation_id"] == "B-1:whoami"


def test_ws_chat_history_reads_one_bounded_tail_of_the_most_recent_turns(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    inner = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    for i in range(60):
        inner.append(
            ChatTurn(
                ts=f"2026-07-27T00:00:{i:02d}+00:00",
                role="operator",
                kind="question",
                text=f"turn-{i}",
            )
        )

    class _RecordingRepo:
        """Pins that the builder asks the STORE for a bounded window.

        Without this, a `tail()[-50:]` implementation would pass the assertions below
        while reading the whole 2000-line file on every connect.
        """

        def __init__(self, delegate):
            self._delegate = delegate
            self.max_lines_seen = None

        def tail(self, max_lines=None):
            self.max_lines_seen = max_lines
            return self._delegate.tail(max_lines=max_lines)

    repo = _RecordingRepo(inner)
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)
        frame = _receive_json(ws)
    assert repo.max_lines_seen == 50  # one bounded read, not a full read then a slice
    texts = [t["text"] for t in frame["payload"]["turns"]]
    assert len(texts) == 50
    assert texts[0] == "turn-10"  # the MOST RECENT 50...
    assert texts[-1] == "turn-59"  # ...still oldest-first


def test_ws_chat_history_survives_a_failing_repo(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _BoomRepo:
        def tail(self, max_lines=None):
            raise OSError("transcript unreadable")

    client.app.state.chat_history_repo = _BoomRepo()
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)
        frame = _receive_json(ws)
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []


def test_ws_chat_history_survives_a_repo_returning_non_turns(client):
    """The connect must not crash on a repo that returns something unexpected.

    ``asdict`` raises TypeError on a plain dict, and the endpoint's outer try catches
    only WebSocketDisconnect, so an unguarded mapping would close the socket with 1011
    and drop the TUI into its reconnect loop.
    """
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _WrongShapeRepo:
        def tail(self, max_lines=None):
            return [{"ts": "t", "role": "operator", "kind": "question", "text": "not a turn"}]

    client.app.state.chat_history_repo = _WrongShapeRepo()
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)
        frame = _receive_json(ws)
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []
