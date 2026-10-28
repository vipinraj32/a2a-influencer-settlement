"""In-process pub/sub for live UI updates (Server-Sent Events). Each event names the users allowed
to see it (the campaign's advertiser, the deal's influencer); the stream filters on that."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self, queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size

    def publish(self, event: dict[str, Any], audience: set[str]) -> None:
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):  # a stalled browser misses events, the next refetch catches up
                q.put_nowait({**event, "_audience": audience})

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)
