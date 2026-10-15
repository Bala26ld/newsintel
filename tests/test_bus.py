import asyncio

import pytest

from newsintel.bus.memory import MemoryBus


async def _roundtrip(bus):
    await bus.publish("t", "k1", {"a": 1})  # no subscriber yet: history only
    sub = bus.subscribe("t", "g1")
    await bus.publish("t", "k2", {"a": 2})
    msg = await asyncio.wait_for(sub.__anext__(), timeout=1)
    return msg


def test_memory_bus_delivers_and_dead_letters():
    bus = MemoryBus()
    msg = asyncio.run(_roundtrip(bus))
    assert msg.key == "k2"

    async def _fail():
        for _ in range(3):
            await bus.fail(msg, "g1", "boom", max_attempts=3)

    asyncio.run(_fail())
    assert bus.dead and bus.dead[0][0].key == "k2"


@pytest.mark.skipif(True, reason="needs a running Redis; run manually with NEWSINTEL_BUS=redis")
def test_redis_bus():
    pass
