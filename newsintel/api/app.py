"""Delivery API: JSON endpoints, SSE live stream, review endpoints, and the plain feed page.

Auth is a static API key per role for the MVP (NEWSINTEL_API_KEYS_READER / _REVIEWER).
`?run=<run_id>` views a replay run instead of live data; the default is live.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from newsintel.bus.base import ALERT_CORRECTED, ALERT_PUBLISHED, STORY_UPDATED, Bus, make_bus
from newsintel.core import clock
from newsintel.core.config import settings
from newsintel.core.dates import parse_datetime
from newsintel.core.ids import derive
from newsintel.ingest.scheduler import source_health
from newsintel.pipeline import review as review_mod
from newsintel.store import models, repo
from newsintel.store.db import current_run_id, init_db, session

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class WatchlistIn(BaseModel):
    name: str
    entity_ids: list[str] = []
    event_types: list[str] = []
    markets: list[str] = []
    min_band: str = "low"


class ReviewDecision(BaseModel):
    decision: str  # approve | reject
    note: str | None = None


def create_app(bus: Bus | None = None, on_startup=None, on_shutdown=None) -> FastAPI:
    """`on_startup(app)` / `on_shutdown(app)` are optional coroutines run inside the lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        app.state.bus = bus or make_bus(settings().bus, settings().redis_url)
        app.state.loop = asyncio.get_running_loop()
        if on_startup:
            await on_startup(app)
        yield
        if on_shutdown:
            await on_shutdown(app)
        await app.state.bus.close()

    app = FastAPI(title="newsintel", version="0.1.0", lifespan=lifespan)

    # ---------- auth ----------

    def role(x_api_key: str | None = Header(default=None), api_key: str | None = Query(default=None)) -> str:
        key = x_api_key or api_key
        s = settings()
        if key == s.api_keys_reviewer:
            return "reviewer"
        if key == s.api_keys_reader:
            return "reader"
        raise HTTPException(401, "missing or invalid API key")

    def reviewer(r: str = Depends(role)) -> str:
        if r != "reviewer":
            raise HTTPException(403, "reviewer key required")
        return r

    @app.middleware("http")
    async def _run_scope(request: Request, call_next):
        token = current_run_id.set(request.query_params.get("run", "live"))
        try:
            return await call_next(request)
        finally:
            current_run_id.reset(token)

    # ---------- pages ----------

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "feed.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        from fastapi.responses import Response

        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#2457d6"/><circle cx="16" cy="16" r="6" fill="#fff"/></svg>'
        return Response(svg, media_type="image/svg+xml")

    # ---------- alerts ----------

    def _alert_out(a: models.Alert) -> dict:
        body = dict(a.body)
        body["row_id"] = a.id
        return body

    @app.get("/v1/alerts")
    async def list_alerts(
        _: str = Depends(role),
        market: str | None = None,
        entity: str | None = None,
        event_type: str | None = None,
        min_band: str = "low",
        since: str | None = None,
        status: str | None = None,
        corrections_only: bool = False,
        q: str | None = Query(None, description="case-insensitive text match on headline and entity"),
        sort: str = Query("newest", pattern="^(newest|materiality)$"),
        limit: int = Query(50, le=500),
    ):
        bands = {"low": 0.0, "medium": 0.35, "high": 0.6}
        with session() as s:
            rows = repo.list_alerts(s, market=market, entity_id=entity, event_type=event_type, min_materiality=bands.get(min_band, 0.0), since=parse_datetime(since), limit=500)
            if status:
                rows = [a for a in rows if a.status == status]
            if corrections_only:
                rows = [a for a in rows if a.correction_of_alert_id]
            if q:
                needle = q.lower()
                rows = [a for a in rows if needle in a.headline.lower() or needle in (a.body.get("entity", {}).get("name") or "").lower() or needle == (a.body.get("entity", {}).get("ticker") or "").lower()]
            if sort == "materiality":
                rows.sort(key=lambda a: (-a.materiality, a.published_at))
            rows = rows[:limit]
            return {"alerts": [_alert_out(a) for a in rows], "count": len(rows)}

    @app.get("/v1/runs")
    async def list_runs(_: str = Depends(role)):
        """Every run id with alerts (live plus replay runs), newest activity first."""
        from sqlalchemy import func

        with session() as s:
            q = select(models.Alert.run_id, func.count(models.Alert.id), func.max(models.Alert.published_at)).group_by(models.Alert.run_id)
            runs = [{"run_id": r, "alerts": n, "last_published_at": clock.iso(t)} for r, n, t in s.execute(q)]
        runs.sort(key=lambda r: (r["run_id"] != "live", r["last_published_at"] or ""), reverse=False)
        runs.sort(key=lambda r: r["last_published_at"] or "", reverse=True)
        return {"runs": runs, "current": current_run_id.get()}

    @app.get("/v1/stats")
    async def stats(_: str = Depends(role)):
        """Counts for the current run: alerts by band, corrections, held for review, sources, last alert."""
        from sqlalchemy import func

        run = current_run_id.get()
        with session() as s:
            total = s.scalar(select(func.count(models.Alert.id)).where(models.Alert.run_id == run)) or 0
            by_band = {"high": 0, "medium": 0, "low": 0}
            for a in s.scalars(select(models.Alert).where(models.Alert.run_id == run)):
                by_band[a.body.get("materiality_band", "low")] += 1
            corrections = s.scalar(select(func.count(models.Alert.id)).where(models.Alert.run_id == run, models.Alert.correction_of_alert_id.is_not(None))) or 0
            held = s.scalar(select(func.count(models.ReviewItem.id)).where(models.ReviewItem.run_id == run, models.ReviewItem.decided_at.is_(None))) or 0
            last = s.scalar(select(func.max(models.Alert.published_at)).where(models.Alert.run_id == run))
            events = s.scalar(select(func.count(models.Event.id)).where(models.Event.run_id == run)) or 0
            markets = {m for (m,) in s.execute(select(models.Alert.market).where(models.Alert.run_id == run).distinct())}
        srcs = source_health()
        return {
            "run": run,
            "alerts": total,
            "by_band": by_band,
            "corrections": corrections,
            "held_for_review": held,
            "events": events,
            "markets": sorted(markets),
            "sources_enabled": sum(1 for x in srcs if x["enabled"]),
            "sources_failing": sum(1 for x in srcs if x["consecutive_failures"] > 0),
            "last_published_at": clock.iso(last),
        }

    @app.get("/v1/held")
    async def held_events(_: str = Depends(role)):
        """Events the gate did not publish (held, merged, suppressed), with the rule that decided it. Read-only."""
        run = current_run_id.get()
        with session() as s:
            out = []
            for ev in s.scalars(select(models.Event).where(models.Event.run_id == run).order_by(models.Event.first_detected_at.desc())):
                gate = [d for d in repo.decisions_for(s, ev.id) if d.stage == "gate"]
                if not gate or gate[-1].outcome == "publish":
                    continue
                out.append({"event_id": ev.id, "event_type": ev.event_type, "market": ev.market, "entity": ev.payload.get("primary_entity"), "status": ev.status, "materiality": ev.materiality, "detected_at": clock.iso(ev.first_detected_at), "outcome": gate[-1].outcome, "rule_id": gate[-1].rule_id, "reason": gate[-1].reason})
            return {"items": out}

    @app.get("/v1/alerts/{alert_id}")
    async def get_alert(alert_id: str, _: str = Depends(role)):
        with session() as s:
            rows = repo.alert_versions(s, alert_id)
            if not rows:
                raise HTTPException(404, "alert not found")
            return {"alert_id": alert_id, "versions": [_alert_out(a) for a in rows]}

    @app.get("/v1/stories/{story_id}")
    async def get_story(story_id: str, _: str = Depends(role)):
        with session() as s:
            story = s.get(models.Story, story_id)
            if not story:
                raise HTTPException(404, "story not found")
            events = list(s.scalars(select(models.Event).where(models.Event.story_id == story_id, models.Event.run_id == current_run_id.get()).order_by(models.Event.first_detected_at)))
            alerts = list(s.scalars(select(models.Alert).where(models.Alert.story_id == story_id, models.Alert.run_id == current_run_id.get()).order_by(models.Alert.published_at)))
            return {
                "story_id": story_id,
                "title": story.title,
                "timeline": [{"kind": "event", "event_id": e.id, "status": e.status, "at": clock.iso(e.first_detected_at), "event_type": e.event_type} for e in events]
                + [{"kind": "alert", "row_id": a.id, "version": a.version, "at": clock.iso(a.published_at), "headline": a.headline, "correction": bool(a.correction_of_alert_id)} for a in alerts],
            }

    @app.get("/v1/events/{event_id}/evidence")
    async def get_evidence(event_id: str, _: str = Depends(role)):
        with session() as s:
            ev = s.get(models.Event, event_id)
            if not ev:
                raise HTTPException(404, "event not found")
            claims = repo.claims_for(s, event_id)
            docs = {}
            for c in claims:
                if c.evidence_doc_id not in docs:
                    d = repo.get_document(s, c.evidence_doc_id)
                    snap = s.get(models.Snapshot, d.snapshot_id) if d else None
                    docs[c.evidence_doc_id] = {
                        "doc_id": c.evidence_doc_id,
                        "url": snap.url if snap else None,
                        "content_hash": snap.content_hash if snap else None,
                        "retrieved_at": clock.iso(snap.retrieved_at) if snap else None,
                        "source_published_at": clock.iso(snap.source_published_at) if snap else None,
                        "text_available": bool(d and d.text),
                        "meta": d.meta if d else {},
                    }
            return {
                "event": ev.payload,
                "claims": [
                    {
                        "id": c.id,
                        "field": c.field,
                        "value": c.value.get("v"),
                        "unit": c.unit,
                        "doc_id": c.evidence_doc_id,
                        "span": [c.span_start, c.span_end],
                        "span_text": c.span_text,
                        "extractor": c.extractor,
                        "check_status": c.check_status,
                        "checked_by": c.checked_by,
                        "reason": c.reason,
                    }
                    for c in claims
                ],
                "documents": list(docs.values()),
                "decisions": [{"stage": d.stage, "outcome": d.outcome, "reason": d.reason, "rule_id": d.rule_id, "at": clock.iso(d.created_at)} for d in repo.decisions_for(s, event_id)],
            }

    @app.get("/v1/documents/{doc_id}/text")
    async def get_document_text(doc_id: str, _: str = Depends(role)):
        with session() as s:
            d = repo.get_document(s, doc_id)
            if not d:
                raise HTTPException(404, "document not found")
            if d.text is None:
                raise HTTPException(451, "source terms do not permit storing full text; use the evidence link")
            return {"doc_id": doc_id, "text": d.text}

    @app.get("/v1/entities")
    async def entities(q: str = "", _: str = Depends(role)):
        with session() as s:
            rows = [e for e in repo.all_entities(s) if not q or q.lower() in e.canonical_name.lower() or (e.ticker and q.upper() == e.ticker)]
            return {"entities": [{"id": e.id, "name": e.canonical_name, "ticker": e.ticker, "exchange": e.exchange, "country": e.country} for e in rows[:50]]}

    # ---------- watchlists ----------

    @app.post("/v1/watchlists")
    async def create_watchlist(r: str = Depends(role), body: WatchlistIn = Body(...)):
        wid = derive("wl", r, body.name, str(uuid.uuid4()))
        with session() as s:
            s.add(models.Watchlist(id=wid, user_id=r, name=body.name, entity_ids=body.entity_ids, event_types=body.event_types, markets=body.markets, min_band=body.min_band))
        return {"id": wid, **body.model_dump()}

    @app.get("/v1/watchlists/{wid}")
    async def get_watchlist(wid: str, _: str = Depends(role)):
        with session() as s:
            w = s.get(models.Watchlist, wid)
            if not w:
                raise HTTPException(404, "watchlist not found")
            return {"id": w.id, "name": w.name, "entity_ids": w.entity_ids, "event_types": w.event_types, "markets": w.markets, "min_band": w.min_band}

    # ---------- live stream ----------

    @app.get("/v1/stream")
    async def stream(request: Request, _: str = Depends(role), watchlist: str | None = None, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
        run = current_run_id.get()
        filt = None
        if watchlist:
            with session() as s:
                w = s.get(models.Watchlist, watchlist)
                filt = {"entity_ids": set(w.entity_ids), "event_types": set(w.event_types), "markets": set(w.markets)} if w else None

        def allowed(p: dict) -> bool:
            if not filt:
                return True
            return (
                (not filt["entity_ids"] or p.get("entity_id") in filt["entity_ids"])
                and (not filt["event_types"] or p.get("event_type") in filt["event_types"])
                and (not filt["markets"] or p.get("market") in filt["markets"])
            )

        async def gen():
            # 1. catch-up from the store when the client tells us where it was
            if last_event_id:
                token = current_run_id.set(run)
                try:
                    with session() as s:
                        for a in reversed(repo.list_alerts(s, since=parse_datetime(last_event_id), limit=200)):
                            yield {
                                "event": "alert.published",
                                "id": clock.iso(a.published_at),
                                "data": json.dumps(
                                    {
                                        "alert_id": a.alert_id,
                                        "version": a.version,
                                        "headline": a.headline,
                                        "row_id": a.id,
                                        "market": a.market,
                                        "event_type": a.event_type,
                                        "entity_id": a.primary_entity_id,
                                        "band": a.body.get("materiality_band"),
                                        "published_at": clock.iso(a.published_at),
                                    }
                                ),
                            }
                finally:
                    current_run_id.reset(token)
            # 2. live: fan-in the three streams through one queue
            queue: asyncio.Queue = asyncio.Queue()
            group = f"sse-{uuid.uuid4().hex[:8]}"

            async def pump(stream_name: str):
                async for msg in request.app.state.bus.subscribe(stream_name, group):
                    await queue.put((stream_name, msg))
                    await request.app.state.bus.ack(msg, group)

            tasks = [asyncio.create_task(pump(n)) for n in (ALERT_PUBLISHED, ALERT_CORRECTED, STORY_UPDATED)]
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        name, msg = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield {"event": "ping", "data": clock.iso(clock.now())}
                        continue
                    if name != STORY_UPDATED and not allowed(msg.payload):
                        continue
                    yield {"event": name, "id": msg.payload.get("published_at") or msg.produced_at, "data": json.dumps(msg.payload)}
            finally:
                for t in tasks:
                    t.cancel()

        return EventSourceResponse(gen())

    # ---------- health & review (internal) ----------

    @app.get("/v1/health/sources")
    async def health_sources(_: str = Depends(role)):
        return {"sources": source_health(), "at": clock.iso(clock.now())}

    @app.get("/v1/health/providers")
    async def health_providers(_: str = Depends(reviewer)):
        from newsintel.ai.router import Router

        return Router().health()

    @app.get("/v1/review")
    async def review_queue(_: str = Depends(reviewer)):
        with session() as s:
            items = list(s.scalars(select(models.ReviewItem).where(models.ReviewItem.run_id == current_run_id.get(), models.ReviewItem.decided_at.is_(None)).order_by(models.ReviewItem.created_at)))
            out = []
            for it in items:
                ev = s.get(models.Event, it.event_id)
                out.append(
                    {
                        "review_id": it.id,
                        "event_id": it.event_id,
                        "reason": it.reason,
                        "created_at": clock.iso(it.created_at),
                        "event": ev.payload if ev else None,
                        "claims": [{"field": c.field, "value": c.value.get("v"), "span_text": c.span_text, "check_status": c.check_status} for c in repo.claims_for(s, it.event_id)],
                    }
                )
            return {"items": out}

    @app.post("/v1/review/{review_id}/decision")
    async def review_decide(review_id: str, request: Request, payload: ReviewDecision = Body(...), r: str = Depends(reviewer)):
        body = payload
        if body.decision not in {"approve", "reject"}:
            raise HTTPException(400, "decision must be approve or reject")
        try:
            return await review_mod.decide(request.app.state.bus, review_id, body.decision, reviewer=r, note=body.note)
        except KeyError:
            raise HTTPException(404, "review item not found") from None

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": type(exc).__name__})

    return app


app = create_app()


def utcnow() -> datetime:
    return clock.now()
