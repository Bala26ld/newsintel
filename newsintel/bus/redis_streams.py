"""Redis Streams bus with consumer groups, acks, bounded retries and a dead-letter stream."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from typing import Any

from redis import asyncio as aioredis
from redis.exceptions import ResponseError

from newsintel.bus.base import Message


class RedisStreamsBus:
    def __init__(self, url: str, block_ms: int = 1000, maxlen: int = 100_000) -> None:
        self._r = aioredis.from_url(url, decode_responses=True)
        self._consumer = f"{socket.gethostname()}-{id(self)}"
        self._block_ms = block_ms
        self._maxlen = maxlen

    async def publish(self, stream: str, key: str, payload: dict[str, Any]) -> None:
        msg = Message(stream=stream, key=key, payload=payload)
        await self._r.xadd(stream, {"m": msg.to_json()}, maxlen=self._maxlen, approximate=True)

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._r.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as e:  # BUSYGROUP = already exists
            if "BUSYGROUP" not in str(e):
                raise

    def subscribe(self, stream: str, group: str) -> AsyncIterator[Message]:
        return self._subscribe(stream, group)

    async def _subscribe(self, stream: str, group: str) -> AsyncIterator[Message]:
        await self._ensure_group(stream, group)
        while True:
            # First drain anything delivered to this consumer but never acked, then read new.
            for start in ("0", ">"):
                res = await self._r.xreadgroup(group, self._consumer, {stream: start}, count=10, block=self._block_ms if start == ">" else None)
                for _, entries in res or []:
                    for entry_id, fields in entries:
                        yield Message.from_json(stream, fields["m"], id=entry_id)
                if start == "0" and res and any(entries for _, entries in res):
                    break

    async def ack(self, msg: Message, group: str) -> None:
        if msg.id:
            await self._r.xack(msg.stream, group, msg.id)

    async def fail(self, msg: Message, group: str, error: str, max_attempts: int = 3) -> None:
        msg.attempts += 1
        if msg.id:
            await self._r.xack(msg.stream, group, msg.id)
        if msg.attempts >= max_attempts:
            await self._r.xadd(msg.stream + ".dead", {"m": msg.to_json(), "error": error[:512]}, maxlen=self._maxlen, approximate=True)
            return
        await self._r.xadd(msg.stream, {"m": msg.to_json()}, maxlen=self._maxlen, approximate=True)

    async def close(self) -> None:
        await self._r.aclose()
