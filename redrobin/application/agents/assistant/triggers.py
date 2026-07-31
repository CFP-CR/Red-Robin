"""Signal-not-noise gate — the cheap deterministic filters (spec §6.A).

``should_speak`` is a pure check (dedup + novelty + cooldown); ``record_spoken``
commits an utterance so the NEXT cycle dedups/cools-down against it. The split
means a cycle that stays silent (the model returns nothing material) never burns
the cooldown or dedup — only a real recommendation does. The model-assessed
materiality threshold is the second stage and lives in the model, not here.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class TriggerGate:
    _CORR_CACHE_MAX = 256

    def __init__(
        self, *, cooldown_sec: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._cooldown = cooldown_sec
        self._clock = clock
        self._seen_corr: OrderedDict[str, None] = OrderedDict()
        self._last_utterance_at: float | None = None
        self._last_state_sig: str | None = None

    def should_speak(self, *, correlation_id: str | None, state_signature: str) -> bool:
        # dedup: never re-speak for a correlation we've already spoken on
        if correlation_id is not None and correlation_id in self._seen_corr:
            return False
        # novelty: stay quiet if nothing changed since the last utterance
        if state_signature == self._last_state_sig:
            return False
        # cooldown: at most one utterance per cooldown window
        if self._last_utterance_at is not None:
            if (self._clock() - self._last_utterance_at) < self._cooldown:
                return False
        return True

    def record_spoken(self, *, correlation_id: str | None, state_signature: str) -> None:
        """Commit an utterance. Called ONLY after a recommendation is published."""
        self._last_utterance_at = self._clock()
        self._last_state_sig = state_signature
        if correlation_id is not None:
            self._seen_corr[correlation_id] = None
            self._seen_corr.move_to_end(correlation_id)
            while len(self._seen_corr) > self._CORR_CACHE_MAX:
                self._seen_corr.popitem(last=False)  # FIFO eviction


__all__ = ["TriggerGate"]
