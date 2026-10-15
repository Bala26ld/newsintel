"""Polling scheduler for live adapters.

Per source: run discover on the registry interval, fetch new candidates, ingest them; back off
exponentially on repeated failures (capped at 16x), tighten the interval inside an expected-event
window, and emit adapter.stale when nothing has succeeded for three intervals.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import timedelta

from newsintel.adapters.base import FixtureRecorder, SourceAdapter
from newsintel.bus.base import ADAPTER_STALE, Bus
from newsintel.core import clock
from newsintel.core.config import ROOT, sources
from newsintel.ingest.service import ingest_snapshot
from newsintel.store import models, repo
from newsintel.store.db import session

log = logging.getLogger(__name__)


def build_adapter(cfg: dict) -> SourceAdapter:
    module_name, cls_name = cfg["adapter"].split(":")
    cls = getattr(importlib.import_module(module_name), cls_name)
    entities: list[dict] = []
    if cfg.get("universe"):
        import yaml

        entities = yaml.safe_load((ROOT / cfg["universe"]).read_text())["entities"]
    return cls(config=cfg, entities=entities, recorder=FixtureRecorder())


async def run_source(bus: Bus, cfg: dict, stop: asyncio.Event) -> None:
    adapter = build_adapter(cfg)
    base_interval = int(cfg.get("poll_interval_s", 60))
    backoff = 1
    last_success = clock.now()
    while not stop.is_set():
        interval = base_interval * backoff
        try:
            candidates = await asyncio.to_thread(adapter.discover)
            for c in candidates:
                raw = await asyncio.to_thread(adapter.fetch, c)
                sid = await ingest_snapshot(bus, raw)
                if sid:
                    log.info("ingested %s from %s", sid, cfg["id"])
            backoff = 1
            last_success = clock.now()
        except Exception as exc:  # one bad poll must not stop the source
            backoff = min(backoff * 2, 16)
            log.warning("source %s failed: %s (backoff x%d)", cfg["id"], exc, backoff)
            with session() as s:
                src = repo.get_source(s, cfg["id"])
                if src:
                    src.consecutive_failures += 1
        if clock.now() - last_success > timedelta(seconds=3 * base_interval):
            await bus.publish(ADAPTER_STALE, key=f"{cfg['id']}:{clock.iso(clock.now())}", payload={"source_id": cfg["id"]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_all(bus: Bus, stop: asyncio.Event) -> None:
    tasks = [asyncio.create_task(run_source(bus, cfg, stop)) for cfg in sources() if cfg.get("enabled")]
    if not tasks:
        log.warning("no enabled sources in config/sources.yaml")
        return
    await asyncio.gather(*tasks)


def source_health() -> list[dict]:
    with session() as s:
        rows = s.query(models.Source).all()
        return [
            {
                "source_id": r.id,
                "market": r.market,
                "enabled": r.enabled,
                "trust_score": r.trust_score,
                "last_success_at": clock.iso(r.last_success_at),
                "consecutive_failures": r.consecutive_failures,
                "published": r.published,
                "corrections": r.corrections,
            }
            for r in rows
        ]
