import asyncio

import pytest

from redrobin.tui.textual_ui import ws_worker
from redrobin.tui.textual_ui.frames import Registry


class _Target:
    pass


def test_build_ws_url_appends_token():
    url = ws_worker.build_ws_url("ws://127.0.0.1:8765/v1/tui", "TOK")
    assert url == "ws://127.0.0.1:8765/v1/tui?token=TOK"


def test_build_ws_url_no_token():
    url = ws_worker.build_ws_url("ws://h/v1/tui", None)
    assert url == "ws://h/v1/tui"


def test_handle_raw_dispatches_valid_frame():
    reg = Registry()
    seen = {}
    reg.register("working_memory_snapshot")(lambda t, p: seen.update(p))
    target = _Target()

    ws_worker.handle_raw(
        target=target,
        raw='{"type":"working_memory_snapshot","payload":{"current_phase":"recon"}}',
        registry=reg,
    )

    assert seen == {"current_phase": "recon"}


def test_handle_raw_ignores_bad_json():
    reg = Registry()
    assert ws_worker.handle_raw(target=None, raw="not json", registry=reg) is False


def test_handle_raw_ignores_unknown_type():
    reg = Registry()
    assert ws_worker.handle_raw(target=None, raw='{"type":"weird"}', registry=reg) is False


@pytest.mark.asyncio
async def test_ws_loop_reports_connecting_then_connected(monkeypatch):
    class _FakeWS:
        async def send(self, data):  # the app stores this as the _ws_send handle
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration  # no frames; connection ends immediately

    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()  # one connection, then the generator is exhausted → loop ends

    monkeypatch.setattr(ws_worker, "connect", _fake_connect)
    states: list[str] = []
    await ws_worker.ws_loop(
        target=_Target(),  # ws_loop does setattr(target, "_ws_send", ...) → needs __dict__
        url="ws://x/v1/tui",
        token=None,
        registry=Registry(),
        stop_event=asyncio.Event(),
        set_state=states.append,
    )
    assert states[:2] == ["connecting", "connected"]


@pytest.mark.asyncio
async def test_ws_loop_caps_inbound_frames_above_the_library_default(monkeypatch):
    """``connect`` must be given an explicit ``max_size`` above ``websockets``' 1 MiB default.

    The daemon's connect frames are bounded by item count, not bytes: ``chat_history`` sends up
    to 50 whole turns and one can be an arbitrary operator paste. On the library default an
    oversized frame makes the client close with 1009 "message too big"; ``ws_loop``'s handler
    reconnects with no backoff, the daemon re-sends the same frame, and the pane never renders it.

    Pins the kwarg itself, because that failure is invisible to every other test here: dropping
    ``max_size=`` from the call restores a silent hot reconnect loop against a real daemon while
    the rest of the suite (which never sends a frame anywhere near 1 MiB) stays green.
    """
    seen: dict[str, object] = {}

    class _FakeWS:
        async def send(self, data):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    async def _fake_connect(*args, **kwargs):
        seen.update(kwargs)
        yield _FakeWS()

    monkeypatch.setattr(ws_worker, "connect", _fake_connect)
    await ws_worker.ws_loop(
        target=_Target(),
        url="ws://x/v1/tui",
        token=None,
        registry=Registry(),
        stop_event=asyncio.Event(),
    )

    # kwargs alone are sufficient: ``max_size`` is KEYWORD-ONLY on websockets' client, so it
    # cannot be smuggled past this check positionally.
    assert "max_size" in seen, "ws_loop must not leave max_size at the websockets default"
    cap = seen["max_size"]
    # An int, never None: an unbounded client would let a broken or hostile daemon OOM the TUI.
    assert isinstance(cap, int) and not isinstance(cap, bool)
    assert cap > 1024 * 1024, "the cap must sit above the 1 MiB default that hot-loops"
