import asyncio
import json

import httpx
import pytest

from newsintel.api.app import create_app
from newsintel.bus.base import ALERT_PUBLISHED
from newsintel.bus.memory import MemoryBus
from newsintel.replay.runner import replay

H = {"X-API-Key": "dev-reader-key"}
R = {"X-API-Key": "dev-reviewer-key"}


@pytest.fixture(scope="module")
def app_and_bus():
    asyncio.run(replay("test-api"))
    bus = MemoryBus()
    return create_app(bus), bus


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_alerts_evidence_and_auth(app_and_bus):
    app, _ = app_and_bus

    async def go():
        async with app.router.lifespan_context(app):
            async with await _client(app) as c:
                r = await c.get("/v1/alerts", params={"run": "test-api"}, headers=H)
                assert r.status_code == 200 and r.json()["count"] >= 10
                a = r.json()["alerts"][0]
                ev = (await c.get(f"/v1/events/{a['event_id']}/evidence", params={"run": "test-api"}, headers=H)).json()
                assert ev["claims"] and ev["documents"][0]["url"].startswith("https://www.sec.gov/")
                assert (await c.get("/v1/alerts", headers={"X-API-Key": "nope"})).status_code == 401
                assert (await c.get("/v1/review", headers=H)).status_code == 403
                st = (await c.get(f"/v1/stories/{a['story_id']}", params={"run": "test-api"}, headers=H)).json()
                assert st["timeline"]

    asyncio.run(go())


def test_review_approval_publishes_through_gate(app_and_bus):
    app, _ = app_and_bus

    async def go():
        async with app.router.lifespan_context(app):
            async with await _client(app) as c:
                items = (await c.get("/v1/review", params={"run": "test-api"}, headers=R)).json()["items"]
                assert len(items) == 1 and "60%" in items[0]["reason"]
                r = await c.post(f"/v1/review/{items[0]['review_id']}/decision", params={"run": "test-api"}, json={"decision": "approve", "note": "ok"}, headers=R)
                assert r.json()["status"] == "published"
                again = await c.post(f"/v1/review/{items[0]['review_id']}/decision", params={"run": "test-api"}, json={"decision": "approve"}, headers=R)
                assert again.json()["status"] == "already_decided"

    asyncio.run(go())


def test_sse_live_delivery(app_and_bus):
    """httpx's ASGI transport buffers responses, so the live stream is tested against a real server."""
    import socket
    import threading
    import time

    import uvicorn

    app, bus = app_and_bus
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    got = []

    def reader():
        with httpx.Client(timeout=10) as c, c.stream("GET", f"http://127.0.0.1:{port}/v1/stream", params={"run": "test-api"}, headers=H) as s:
            for line in s.iter_lines():
                if line.startswith("data:") and "headline" in line:
                    got.append(json.loads(line[5:]))
                    return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.5)
    payload = {"headline": "live test alert", "market": "US", "event_type": "earnings", "entity_id": "e", "published_at": "2026-10-15T00:00:00Z"}
    asyncio.run_coroutine_threadsafe(app.state.bus.publish(ALERT_PUBLISHED, key="x", payload=payload), app.state.loop).result(timeout=5)
    t.join(timeout=5)
    server.should_exit = True
    assert got and got[0]["headline"] == "live test alert"
