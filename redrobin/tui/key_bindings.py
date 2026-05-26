"""Key bindings: Enter dispatches input; Ctrl+O toggles inspector; '/' enters slash mode.

Binding semantics:

- ``Enter`` — strip the input buffer; if non-empty, schedule the async dispatch
  callback and clear the buffer for the next command. ``on_submit`` routes
  slash-prefixed input through ``slash_commands.parse``.
- ``Ctrl+O`` — toggle the memory-inspector overlay. Pressed twice it closes.
- ``/`` — when pressed on an empty buffer, seed a leading ``/`` so the
  operator types the verb directly (``/alts``, ``/inspector``, ...). When the
  buffer already contains text, ``/`` is inserted as a literal so it doesn't
  interrupt mid-typing. Prefix is intentionally NOT coupled to a palette
  overlay; the slash text is recognised by ``parse_slash`` on submit.
- ``Escape`` — close any active overlay.
- ``Ctrl+C`` — clean Application.exit().

Deliberately not bound: ``Ctrl+I`` — that is the terminal-protocol equivalent of
``Tab`` and binding it breaks tab traversal on Windows terminals.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings

from redrobin.tui.state import TUIState


def build_key_bindings(
    *,
    state: TUIState,
    input_buffer: Buffer,
    on_submit: Callable[[str], Awaitable[None]],
    invalidate: Callable[[], None],
) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter")
    def _(event) -> None:
        text = input_buffer.text.strip()
        if not text:
            return
        input_buffer.reset()
        # create_background_task is the prompt_toolkit ≥3.0.50 entry point for
        # spawning coroutines tied to the application lifetime — the task is
        # cancelled when the app exits, so we don't leak coroutines.
        event.app.create_background_task(on_submit(text))
        invalidate()

    @kb.add("c-o")
    def _(event) -> None:
        state.focus_overlay = "inspector" if state.focus_overlay != "inspector" else None
        invalidate()

    @kb.add("/")
    def _(event) -> None:
        # When the buffer is empty, ``/`` is the slash-mode trigger: seed the
        # leading slash so on_submit can dispatch through the slash parser.
        # When mid-typing, fall back to inserting ``/`` as a literal so it
        # doesn't disrupt the input flow.
        if not input_buffer.text:
            input_buffer.text = "/"
            input_buffer.cursor_position = 1
        else:
            input_buffer.insert_text("/")
        invalidate()

    @kb.add("escape")
    def _(event) -> None:
        state.focus_overlay = None
        invalidate()

    @kb.add("c-c")
    def _(event) -> None:
        event.app.exit()

    return kb
