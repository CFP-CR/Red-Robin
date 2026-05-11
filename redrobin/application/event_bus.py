"""In-process pub/sub bus. Single writer, many readers, bounded queues."""
from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    """Fan-out bus: every subscriber gets its own bounded asyncio.Queue.

    When a subscriber's queue is full on publish, the oldest message is dropped
    to make room. This protects the producer from a slow/dead consumer at the
    cost of a single event for that consumer.
    """

    def __init__(self, queue_maxsize: int = 256) -> None:
        self._queue_maxsize = queue_maxsize
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._subscribers.append(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subscribers)
        for queue in targets:
            self._enqueue_drop_oldest(queue, event)

    @staticmethod
    def _enqueue_drop_oldest(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def close(self) -> None:
        async with self._lock:
            targets = list(self._subscribers)
            self._subscribers.clear()
        for queue in targets:
            self._enqueue_drop_oldest(queue, {"_type": "_close"})
