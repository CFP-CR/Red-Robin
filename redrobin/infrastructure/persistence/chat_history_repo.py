"""redrobin/infrastructure/persistence/chat_history_repo.py — the chat transcript store.

Append-only JSONL, one :class:`~redrobin.domain.chat.ChatTurn` per line, one file per
engagement (a sibling of ``events.jsonl``). Deliberately shaped like a LOG, not like the
document repos (``WorkingMemoryRepo``): a turn is appended once and never rewritten, so
there is no atomic whole-file replace.

Reads are bounded: ``tail`` streams the file through a ``deque`` so memory is
O(max_lines), never O(file). A malformed line — a truncated append, a missing field — is
skipped so one bad entry cannot break the read. Neither method raises on a missing file.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import asdict
from pathlib import Path

from redrobin.domain.chat import ChatTurn

logger = logging.getLogger(__name__)

# Memory safety valve for a pathologically long log: the read is O(max_lines), never
# O(file). PUBLIC (exported below) because ``application.chat.history`` passes it to
# ``tail`` explicitly so a saturated read is observable there.
#
# It bounds the READ, and through it the selection: the selector only ever sees these last
# lines, so a tail SATURATED with one kind starves the other window's quota — e.g. 2000
# consecutive advisories leave zero dialogue turns to select even though dialogue exists
# earlier in the file, silently degrading to pre-feature behaviour. The cap therefore sits
# far above both quotas (10 + 5 by default) so that needs a genuinely pathological tail;
# ``build_history_reader`` logs a debug line if it ever happens. One file per engagement
# spans daemon restarts, so this is a real (if unlikely) shape, not a theoretical one.
#
# COST OF THE CAP, measured (2026-07-27, CPython 3.13 / Windows 11, warm page cache, a
# realistic 2000-line transcript — mixed questions/answers/composed advisories, 534 KB):
# ``tail`` medians 7.5 ms, p95 10.1 ms; at the pre-2026-07-25 cap of 500 it was 1.8 ms.
# That read is SYNCHRONOUS on the event loop and runs once per reactive cycle, i.e. once
# per beacon command — but the cycle it feeds is an LLM round-trip measured in SECONDS,
# so ~7 ms is three orders of magnitude below the work it precedes. Left synchronous and
# left at 2000 deliberately; revisit (thread-offload, or an in-process tail cache) only
# if the cap grows again or a profile shows the loop stalling.
MAX_TAIL_LINES = 2000


def _parse_line(raw: str) -> ChatTurn | None:
    """One JSONL line -> ChatTurn, or None when unusable. Never raises.

    NOTE: the line's content is never logged — a turn's text can contain credentials.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("chat_history: skipped a malformed line")
        return None
    if not isinstance(data, dict):
        logger.debug("chat_history: skipped a line whose JSON is not a JSON object")
        return None
    try:
        correlation_id = data.get("correlation_id")
        return ChatTurn(
            ts=str(data["ts"]),
            role=str(data["role"]),
            kind=str(data["kind"]),
            text=str(data["text"]),
            correlation_id=str(correlation_id) if correlation_id is not None else None,
        )
    except KeyError:
        logger.debug("chat_history: skipped a line missing a required field")
        return None


class ChatHistoryRepo:
    """One engagement's chat transcript (append-only JSONL).

    Single-writer by contract: only ``ChatRecorder`` calls :meth:`append` — the same
    invariant the working memory holds with the reducer as its sole writer.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def append(self, turn: ChatTurn) -> None:
        """Append one turn as a single JSON line. Creates the parent dir if needed.

        ``errors="replace"`` matches the read path: a lone surrogate in a turn's text (a
        mangled decode upstream) would otherwise raise ``UnicodeEncodeError``, which the
        recorder's never-crash guard absorbs — so the loop would survive but the turn would
        be silently LOST. A substituted character is strictly better than a dropped turn.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(turn), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")

    def tail(self, max_lines: int = MAX_TAIL_LINES) -> list[ChatTurn]:
        """The parseable turns among the last ``max_lines`` LINES, oldest first.

        NOT "the last ``max_lines`` parseable turns": the cap bounds the lines READ, and
        an unparseable line among them is dropped rather than backfilled, so the result
        can be shorter than ``max_lines`` even on a much longer file. Missing file -> [].
        """
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            raw_lines = deque(fh, maxlen=max_lines)
        turns: list[ChatTurn] = []
        for raw in raw_lines:
            turn = _parse_line(raw)
            if turn is not None:
                turns.append(turn)
        return turns


__all__ = ["MAX_TAIL_LINES", "ChatHistoryRepo"]
