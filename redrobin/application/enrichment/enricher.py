"""WorkingMemoryEnricher — async bus consumer that turns beacon command output
into Working Memory facts via an LLM, then PUBLISHES a working_memory.deltas event
(Plan A's reducer is the single writer that merges it). Never writes the WM, never
crashes the daemon, never blocks the gate. One retry on malformed JSON, then drop.
Emits enrichment.completed / enrichment.failed for observability (tui_ws forwards
all bus events to the TUI).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from redrobin.application.enrichment.chunker import chunk_output
from redrobin.application.enrichment.llm import EnricherLLM
from redrobin.application.enrichment.parser import DeltaParseError, parse_deltas
from redrobin.application.enrichment.trigger import should_enrich
from redrobin.application.event_bus import EventBus
from redrobin.domain.memory import WorkingMemoryDeltas

logger = logging.getLogger(__name__)


class WorkingMemoryEnricher:
    def __init__(
        self,
        *,
        bus: EventBus,
        llm: EnricherLLM | None,
        denylist: list[str],
        min_output_chars: int,
        chunk_max_chars: int,
        enabled: bool = True,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._denylist = denylist
        self._min_output_chars = min_output_chars
        self._chunk_max_chars = chunk_max_chars
        self._enabled = enabled
        self.on_started: Callable[[], None] | None = None

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("enricher on_started callback raised")
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                if evt.get("type") != "beacon.command_output":
                    continue
                try:
                    await self._enrich(evt.get("payload") or {})
                except Exception:
                    logger.exception("enricher failed to process beacon.command_output")
        finally:
            await self._bus.unsubscribe(sub)

    async def _enrich(self, payload: dict[str, Any]) -> None:
        if self._llm is None or not self._enabled:
            return
        command = str(payload.get("command") or payload.get("cmd") or "")
        output = str(payload.get("output", ""))
        bid = payload.get("bid")
        src = str(bid) if bid is not None else None
        if not should_enrich(
            command, output, denylist=self._denylist, min_output_chars=self._min_output_chars
        ):
            return

        t0 = time.monotonic()
        chunks = chunk_output(output, max_chars=self._chunk_max_chars)
        try:
            per_chunk = await asyncio.gather(
                *(self._extract_chunk(command, src, c) for c in chunks)
            )
        except Exception as exc:  # network/timeout/api — best-effort
            await self._emit_failed(command, src, f"llm_error: {exc}")
            return

        # All chunks unparseable (even after the per-chunk retry) → fail loud,
        # do NOT publish an empty deltas event. Matches test_enricher_malformed_json.
        # `per_chunk and` is defensive: chunk_output never returns [] today (output
        # already passed should_enrich's min-length floor), but the guard stops a
        # future empty-chunks change from making all() vacuously True → spurious fail.
        if per_chunk and all(d is None for d in per_chunk):
            await self._emit_failed(command, src, "all_chunks_unparseable")
            return

        merged = WorkingMemoryDeltas()
        for deltas in per_chunk:
            if deltas is None:
                continue
            merged.hosts.extend(deltas.hosts)
            merged.credentials.extend(deltas.credentials)
            merged.findings.extend(deltas.findings)
            merged.tools_used.extend(deltas.tools_used)
            merged.defenses_observed.extend(deltas.defenses_observed)

        n = (
            len(merged.hosts)
            + len(merged.credentials)
            + len(merged.findings)
            + len(merged.tools_used)
            + len(merged.defenses_observed)
        )
        out_payload = merged.to_payload()
        out_payload["source_beacon_id"] = src
        await self._bus.publish({"type": "working_memory.deltas", "payload": out_payload})
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "enrichment ok: command=%s beacon=%s chunks=%d deltas=%d latency_ms=%d",
            command,
            src,
            len(chunks),
            n,
            latency_ms,
        )
        await self._bus.publish(
            {
                "type": "enrichment.completed",
                "payload": {
                    "command": command,
                    "beacon_id": src,
                    "chunks": len(chunks),
                    "n_deltas": n,
                    "latency_ms": latency_ms,
                },
            }
        )

    async def _extract_chunk(
        self, command: str, src: str | None, chunk: str
    ) -> WorkingMemoryDeltas | None:
        """Extract one chunk; ONE retry on malformed JSON, then log + drop (None)."""
        assert self._llm is not None
        for attempt in (1, 2):
            raw = await self._llm.extract(command=command, beacon_id=src, output=chunk)
            try:
                return parse_deltas(raw, source_beacon_id=src)
            except DeltaParseError as exc:
                if attempt == 2:
                    logger.warning("enrichment: unparseable LLM output, dropping chunk: %s", exc)
                    return None
        return None

    async def _emit_failed(self, command: str, src: str | None, reason: str) -> None:
        logger.error("enrichment failed: command=%s beacon=%s reason=%s", command, src, reason)
        await self._bus.publish(
            {
                "type": "enrichment.failed",
                "payload": {"command": command, "beacon_id": src, "reason": reason},
            }
        )


__all__ = ["WorkingMemoryEnricher"]
