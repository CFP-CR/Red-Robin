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
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from redrobin.application.enrichment.chunker import chunk_output
from redrobin.application.enrichment.llm import EnricherLLM
from redrobin.application.enrichment.parser import DeltaParseError, parse_deltas
from redrobin.application.enrichment.sanitize import strip_self_annotations
from redrobin.application.enrichment.trigger import should_enrich
from redrobin.application.event_bus import EventBus
from redrobin.domain.memory import WorkingMemoryDeltas

logger = logging.getLogger(__name__)


class WorkingMemoryEnricher:
    _CORR_CACHE_MAX = 256

    def __init__(
        self,
        *,
        bus: EventBus,
        llm: EnricherLLM | None,
        denylist: list[str],
        min_output_chars: int,
        chunk_max_chars: int,
        enabled: bool = True,
        max_concurrency: int = 4,
        self_annotation_prefix: str = "[Red Robin]",
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._denylist = denylist
        self._min_output_chars = min_output_chars
        self._chunk_max_chars = chunk_max_chars
        self._enabled = enabled
        self._self_annotation_prefix = self_annotation_prefix
        # Bounded correlation_id -> raw command cache (FIFO eviction at cap), and a
        # per-bid last-command fallback for outputs that arrive without (or with an
        # unknown) correlation_id.
        self._cmd_by_corr: OrderedDict[str, str] = OrderedDict()
        self._cmd_by_bid: dict[str, str] = {}
        # Per-bid own-identity cache from beacon.initial (host, user), used to
        # ground each extraction so the LLM doesn't re-extract this beacon's own
        # host/user as a newly-discovered host (#105). One entry per beacon —
        # naturally bounded, no eviction needed.
        self._host_user_by_bid: dict[str, tuple[str | None, str | None]] = {}
        self._sem = asyncio.Semaphore(max_concurrency)
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
                etype = evt.get("type")
                if etype == "beacon.initial":
                    self._remember_session(evt.get("payload") or {})
                    continue
                if etype == "beacon.command_input":
                    self._remember_command(evt.get("payload") or {})
                    continue
                if etype != "beacon.command_output":
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
        # Recover the command behind this output (exact via correlation_id, then
        # per-bid fallback, then ""). A wire payload may also carry "command"
        # directly (older traces / hand-crafted fixtures) — prefer it when the
        # recovery caches have nothing, so existing behavior is preserved.
        command = self._recover_command(payload) or str(
            payload.get("command") or payload.get("cmd") or ""
        )
        # Strip Red Robin's own console annotations before any gate or LLM call,
        # so a self-annotation never becomes a junk finding (Area 3).
        output = strip_self_annotations(
            str(payload.get("output", "")), prefix=self._self_annotation_prefix
        )
        bid = payload.get("bid")
        src = str(bid) if bid is not None else None
        host, user = (
            self._host_user_by_bid.get(src, (None, None)) if src is not None else (None, None)
        )
        if not should_enrich(
            command, output, denylist=self._denylist, min_output_chars=self._min_output_chars
        ):
            return

        t0 = time.monotonic()
        chunks = chunk_output(output, max_chars=self._chunk_max_chars)
        try:
            per_chunk = await asyncio.gather(
                *(self._extract_chunk(command, src, c, host, user) for c in chunks)
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

    def _remember_session(self, payload: dict[str, Any]) -> None:
        """Cache this beacon's own (host, user) from beacon.initial for grounding."""
        bid = payload.get("bid")
        if bid is None:
            return
        host = payload.get("host")
        user = payload.get("user")
        self._host_user_by_bid[str(bid)] = (
            str(host) if host is not None else None,
            str(user) if user is not None else None,
        )

    def _remember_command(self, payload: dict[str, Any]) -> None:
        """Cache the command behind a future output. Prefers the full ``raw``
        command line, then the first-token ``command``, then ``""``. Keyed by
        ``correlation_id`` (exact, FIFO-bounded) with a per-``bid`` last-command
        fallback for outputs that arrive without a correlation id."""
        command = str(payload.get("raw") or payload.get("command") or "")
        corr = payload.get("correlation_id")
        bid = payload.get("bid")
        if bid is not None:
            self._cmd_by_bid[str(bid)] = command
        if corr is not None:
            corr_key = str(corr)
            self._cmd_by_corr[corr_key] = command
            self._cmd_by_corr.move_to_end(corr_key)
            while len(self._cmd_by_corr) > self._CORR_CACHE_MAX:
                self._cmd_by_corr.popitem(last=False)  # evict oldest (FIFO)

    def _recover_command(self, payload: dict[str, Any]) -> str:
        """Recover the command that produced this output: exact correlation_id
        lookup (popped on hit so the entry doesn't leak) → per-bid last command
        → ``""`` (degraded — pre-correlation-id CS, or input not yet seen)."""
        corr = payload.get("correlation_id")
        if corr is not None:
            cmd = self._cmd_by_corr.pop(str(corr), None)
            if cmd is not None:
                return cmd
        bid = payload.get("bid")
        if bid is not None:
            cmd = self._cmd_by_bid.get(str(bid))
            if cmd is not None:
                return cmd
        return ""

    async def _extract_chunk(
        self,
        command: str,
        src: str | None,
        chunk: str,
        host: str | None = None,
        user: str | None = None,
    ) -> WorkingMemoryDeltas | None:
        """Extract one chunk; ONE retry on malformed JSON, then log + drop (None).

        Each LLM call acquires the shared semaphore so a many-chunk output (e.g.
        ``dir /s C:\\Windows``) throttles through at most ``max_concurrency`` calls
        at once instead of fanning the whole batch out and blowing the rate limit
        (Area 2). The SDK's own retry/``retry-after`` handles the rate ceiling."""
        assert self._llm is not None
        for attempt in (1, 2):
            async with self._sem:
                raw = await self._llm.extract(
                    command=command, beacon_id=src, output=chunk, host=host, user=user
                )
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
