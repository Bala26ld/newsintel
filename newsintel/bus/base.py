"""Event bus abstraction.

Streams (names are the contract between stages):
  snapshot.created   -> pipeline worker
  alert.published    -> delivery (SSE), notification subscribers
  alert.corrected    -> delivery
  story.updated      -> delivery
  adapter.stale      -> operations alerting
  <stream>.dead      -> dead-letter copy of any message whose handler failed `max_attempts` times

Consumers are idempotent: every message carries a deterministic `key`, and handlers must treat a
redelivered key as a no-op. The memory bus is for a single process (dev, tests, replay); the Redis
Streams bus is for multi-process deployments. Kafka can replace it behind the same protocol.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from newsintel.core import clock

SNAPSHOT_CREATED = "snapshot.created"
ALERT_PUBLISHED = "alert.published"
ALERT_CORRECTED = "alert.corrected"
STORY_UPDATED = "story.updated"
ADAPTER_STALE = "adapter.stale"
REVIEW_REQUESTED = "review.requested"


@dataclass
class Message:
    stream: str
    key: str
    payload: dict[str, Any]
    attempts: int = 0
    produced_at: str = field(default_factory=lambda: clock.iso(clock.now()) or "")
    id: str | None = None  # broker-assigned

    def to_json(self) -> str:
        return json.dumps({"key": self.key, "payload": self.payload, "attempts": self.attempts, "produced_at": self.produced_at})

    @classmethod
    def from_json(cls, stream: str, raw: str, id: str | None = None) -> Message:
        d = json.loads(raw)
        return cls(stream=stream, key=d["key"], payload=d["payload"], attempts=d.get("attempts", 0), produced_at=d.get("produced_at", ""), id=id)


Handler = Callable[[Message], Awaitable[None]]


class Bus(Protocol):
    async def publish(self, stream: str, key: str, payload: dict[str, Any]) -> None: ...

    def subscribe(self, stream: str, group: str) -> AsyncIterator[Message]: ...  # may be sync-returning an async iterator

    async def ack(self, msg: Message, group: str) -> None: ...

    async def fail(self, msg: Message, group: str, error: str, max_attempts: int = 3) -> None: ...

    async def close(self) -> None: ...


def make_bus(kind: str, redis_url: str | None = None) -> Bus:
    if kind == "memory":
        from newsintel.bus.memory import MemoryBus

        return MemoryBus()
    if kind == "redis":
        from newsintel.bus.redis_streams import RedisStreamsBus

        return RedisStreamsBus(redis_url or "redis://localhost:6379/0")
    raise ValueError(f"unknown bus kind {kind!r}")
