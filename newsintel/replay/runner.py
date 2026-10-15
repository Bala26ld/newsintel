"""Replay runner: feeds recorded fixtures through ingest and the pipeline with a frozen clock.

Every row the run produces carries `run_id`, so the run is isolated from live data and from
other runs, and two runs can be diffed (differ.py).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from newsintel.adapters.base import RawSnapshot, load_fixtures
from newsintel.bus.base import ALERT_CORRECTED, ALERT_PUBLISHED
from newsintel.bus.memory import MemoryBus
from newsintel.core import clock
from newsintel.core.config import settings
from newsintel.ingest.service import ingest_snapshot
from newsintel.pipeline.worker import PipelineWorker
from newsintel.store.db import current_run_id, init_db


@dataclass
class RunSummary:
    run_id: str
    fixtures: int = 0
    ingested: int = 0
    outcomes: Counter = field(default_factory=Counter)
    alerts: list[dict] = field(default_factory=list)
    latencies_s: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        lat = sorted(self.latencies_s)
        p95 = lat[int(len(lat) * 0.95) - 1] if len(lat) >= 2 else (lat[0] if lat else None)
        return {
            "run_id": self.run_id,
            "fixtures": self.fixtures,
            "ingested": self.ingested,
            "outcomes": dict(self.outcomes),
            "alerts": self.alerts,
            "processing_latency_p95_s": p95,
        }


async def replay(
    run_id: str, fixture_root: Path | None = None, source_id: str | None = None, snapshots: list[RawSnapshot] | None = None, bus: MemoryBus | None = None, pace: bool = False
) -> RunSummary:
    init_db()
    token = current_run_id.set(run_id)
    try:
        bus = bus or MemoryBus()
        worker = PipelineWorker(bus)
        snaps = snapshots if snapshots is not None else load_fixtures(fixture_root or settings().fixture_dir, source_id)
        summary = RunSummary(run_id=run_id, fixtures=len(snaps))
        prev = None
        for snap in snaps:
            if pace and prev is not None:
                await asyncio.sleep(max(0.0, min((snap.retrieved_at - prev).total_seconds(), 5.0)))
            prev = snap.retrieved_at
            # Clock frozen at the snapshot's retrieval time: the replay reproduces original timestamps.
            with clock.freeze(snap.retrieved_at):
                sid = await ingest_snapshot(bus, snap)
                if sid is None:
                    summary.outcomes["ingest:exact_duplicate"] += 1
                    continue
                summary.ingested += 1
                out = await worker.process(sid)
                summary.outcomes[f"{out.stage}:{out.result}"] += 1
                if out.alert_id:
                    summary.alerts.append({"alert_row": out.alert_id, "event_id": out.event_id})
        # Frozen clock: processing latency is measured in live mode, not in replay.
        summary.latencies_s = [0.0] * len(bus.published(ALERT_PUBLISHED) + bus.published(ALERT_CORRECTED))
        return summary
    finally:
        current_run_id.reset(token)
