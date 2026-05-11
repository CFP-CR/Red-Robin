"""Slice 8 integration — build the TUI application and verify the layout.

The Application itself can't be ``run`` without a real terminal, so we build it
and walk the layout container tree to assert the named panels are reachable.
Task 8.6 adds a true daemon-side roundtrip test below this one.
"""
from __future__ import annotations

import httpx
import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from redrobin.tui.app import build_tui_app
from redrobin.tui.dispatch import OperatorDispatcher
from redrobin.tui.state import TUIState


@pytest.fixture
def pty_input():
    with create_pipe_input() as inp:
        yield inp


@pytest.fixture
def pty_output() -> DummyOutput:
    return DummyOutput()


def _walk(container):
    """Yield every container/window in the layout tree.

    Recurses through ``get_children()`` when present (HSplit/VSplit/FloatContainer/
    ConditionalContainer) and through ``content`` for Window leaves.
    """
    yield container
    if hasattr(container, "get_children"):
        for child in container.get_children():
            yield from _walk(child)
    if hasattr(container, "floats"):
        for f in getattr(container, "floats", []) or []:
            yield from _walk(f.content if hasattr(f, "content") else f)


def _collect_names(container) -> set[str]:
    names: set[str] = set()
    for c in _walk(container):
        name = getattr(c, "_rr_name", None)
        if name:
            names.add(name)
    return names


@pytest.fixture
def state() -> TUIState:
    return TUIState()


@pytest.fixture
def dispatcher() -> OperatorDispatcher:
    client = httpx.AsyncClient()
    return OperatorDispatcher(client=client, base_url="http://127.0.0.1:8765", token=None)


def test_build_tui_app_returns_application_instance(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    assert isinstance(app, Application)


def test_build_tui_app_exposes_three_named_panels(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    names = _collect_names(app.layout.container)
    assert "sessions_panel" in names
    assert "events_panel" in names
    assert "memory_panel" in names


def test_build_tui_app_exposes_input_buffer(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    names = _collect_names(app.layout.container)
    assert "input_buffer" in names


def test_build_tui_app_exposes_inspector_float(state, dispatcher, pty_input, pty_output):
    """Inspector overlay must be reachable in the float container tree."""
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    names = _collect_names(app.layout.container)
    assert "inspector_overlay" in names


def test_build_tui_app_has_key_bindings(state, dispatcher, pty_input, pty_output):
    app = build_tui_app(
        state=state, dispatcher=dispatcher, input=pty_input, output=pty_output
    )
    assert app.key_bindings is not None
    # KeyBindings or merged bindings — both expose .bindings.
    bindings = getattr(app.key_bindings, "bindings", None)
    assert bindings is not None
    assert len(bindings) > 0
