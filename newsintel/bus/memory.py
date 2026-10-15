"""In-process bus: one asyncio queue per (stream, group), fan-out across groups."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from newsintel.bus.base import Message


class MemoryBus:
    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], asyncio.Queue[Message]] = {}
        self._groups: dict[str, set[str]] = defaultdict(set)
        self._history: dict[str, list[Message]] = defaultdict(list)  # for late subscribers / tests
        self.dead: list[tuple[Message, str]] = []

    def _queue(self, stream: str, group: str) -> asyncio.Queue[Message]:
        key = (stream, group)
        if key not in self._queues:
            self._queues[key] = asyncio.Queue()
            self._groups[stream].add(group)
        return self._queues[key]

    async def publish(self, stream: str, key: str, payload: dict[str, Any]) -> None:
        msg = Message(stream=stream, key=key, payload=payload)
        self._history[stream].append(msg)
        for group in list(self._groups.get(stream, ())):
            await self._queue(stream, group).put(Message(stream=stream, key=key, payload=payload, produced_at=msg.produced_at))

    def subscribe(self, stream: str, group: str) -> AsyncIterator[Message]:
        # The queue is registered now, not at the first `await`, so nothing published between
        # subscribe() and the first iteration is lost.
        q = self._queue(stream, group)

        async def _gen():
            while True:
                yield await q.get()

        return _gen()

    async def ack(self, msg: Message, group: str) -> None:
        return None

    async def fail(self, msg: Message, group: str, error: str, max_attempts: int = 3) -> None:
        msg.attempts += 1
        if msg.attempts >= max_attempts:
            self.dead.append((msg, error))
            self._history[msg.stream + ".dead"].append(msg)
            return
        await self._queue(msg.stream, group).put(msg)

    async def close(self) -> None:
        return None

    # test / replay helpers
    def published(self, stream: str) -> list[Message]:
        return list(self._history.get(stream, []))

    def pending(self, stream: str, group: str) -> int:
        return self._queue(stream, group).qsize()
