"""Key bindings smoke test — verifies the expected keys are bound.

Full execution of the key handlers requires a running prompt_toolkit Application,
so we only assert that the bindings are present and call out the right side
effects. The full Enter -> dispatch flow is exercised by the integration test
in Slice 8 Task 8.6.
"""

from __future__ import annotations

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.keys import Keys

from redrobin.tui.key_bindings import build_key_bindings
from redrobin.tui.state import TUIState


async def _noop_submit(_text: str) -> None:
    return None


def _noop_invalidate() -> None:
    return None


def _bound_key_repr(b) -> str:
    """Render a binding's key sequence into a comparable string."""
    parts: list[str] = []
    for k in b.keys:
        # `k` is either a Keys enum or a plain string character.
        if isinstance(k, Keys):
            parts.append(k.value)
        else:
            parts.append(str(k))
    return "+".join(parts)


def test_build_key_bindings_returns_expected_keys():
    state = TUIState()
    buf = Buffer()
    kb = build_key_bindings(
        state=state,
        input_buffer=buf,
        on_submit=_noop_submit,
        invalidate=_noop_invalidate,
    )

    rendered = {_bound_key_repr(b) for b in kb.bindings}
    # Enter sends operator input to the daemon.
    assert any("enter" in r for r in rendered) or Keys.ControlM.value in rendered
    # Ctrl+O toggles the inspector overlay.
    assert Keys.ControlO.value in rendered
    # '/' opens the slash command palette.
    assert "/" in rendered
    # Escape closes any overlay.
    assert Keys.Escape.value in rendered
    # Ctrl+C cleanly exits the application.
    assert Keys.ControlC.value in rendered


def test_build_key_bindings_does_not_bind_ctrl_i():
    """Ctrl+I is the terminal-protocol equivalent of Tab — binding it breaks
    tab traversal on Windows terminals. Explicitly verify we don't accidentally
    take it over."""
    state = TUIState()
    buf = Buffer()
    kb = build_key_bindings(
        state=state,
        input_buffer=buf,
        on_submit=_noop_submit,
        invalidate=_noop_invalidate,
    )
    rendered = {_bound_key_repr(b) for b in kb.bindings}
    assert Keys.ControlI.value not in rendered


def test_build_key_bindings_returns_keybindings_instance():
    from prompt_toolkit.key_binding import KeyBindings

    state = TUIState()
    buf = Buffer()
    kb = build_key_bindings(
        state=state,
        input_buffer=buf,
        on_submit=_noop_submit,
        invalidate=_noop_invalidate,
    )
    assert isinstance(kb, KeyBindings)
