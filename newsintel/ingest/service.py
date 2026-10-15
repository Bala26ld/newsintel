"""Ingestion: dedupe by content hash, persist the raw snapshot, announce it on the bus.

Detection does no parsing. The only work here is hashing, storing and publishing, so that the
detection-to-bus path stays fast and the same regardless of document complexity.
"""

from __future__ import annotations

from newsintel.adapters.base import RawSnapshot
from newsintel.bus.base import SNAPSHOT_CREATED, Bus
from newsintel.core import clock
from newsintel.core.ids import derive
from newsintel.ingest.snapshot_store import LocalObjectStore
from newsintel.store import models, repo
from newsintel.store.db import current_run_id, session


def snapshot_id_for(raw: RawSnapshot) -> str:
    return derive("snp", current_run_id.get(), raw.source_id, raw.content_hash)


async def ingest_snapshot(bus: Bus, raw: RawSnapshot, store: LocalObjectStore | None = None) -> str | None:
    """Returns the snapshot id, or None when this exact content was seen before for this source."""
    store = store or LocalObjectStore()
    sid = snapshot_id_for(raw)
    with session() as s:
        if repo.snapshot_exists(s, raw.source_id, raw.content_hash):
            return None
        key = store.put(store.key_for(raw.source_id, raw.content_hash), raw.body)
        s.add(
            models.Snapshot(
                id=sid,
                source_id=raw.source_id,
                url=raw.url,
                content_hash=raw.content_hash,
                retrieved_at=raw.retrieved_at,
                source_published_at=raw.source_published_at,
                storage_key=key,
                http_meta={"headers": raw.headers, "mime": raw.mime, "meta": raw.meta},
                run_id=current_run_id.get(),
            )
        )
        src = repo.get_source(s, raw.source_id)
        if src:
            src.last_success_at = clock.now()
            src.consecutive_failures = 0
    await bus.publish(
        SNAPSHOT_CREATED,
        key=sid,
        payload={"snapshot_id": sid, "source_id": raw.source_id, "url": raw.url, "retrieved_at": clock.iso(raw.retrieved_at)},
    )
    return sid
