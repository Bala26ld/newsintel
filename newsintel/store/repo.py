"""Repository helpers used by the pipeline and the API.

All reads of pipeline-produced rows are scoped to the current run id (see db.current_run_id).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from newsintel.core import clock, schemas
from newsintel.core.ids import derive
from newsintel.store import models
from newsintel.store.db import current_run_id

# ---------- registry ----------


def get_entity(s: Session, entity_id: str) -> models.Entity | None:
    return s.get(models.Entity, entity_id)


def entity_by_cik(s: Session, cik: str) -> models.Entity | None:
    cik = cik.lstrip("0")
    for row in s.scalars(select(models.Entity).where(models.Entity.cik.is_not(None))):
        if row.cik and row.cik.lstrip("0") == cik:
            return row
    return None


def entity_by_ticker(s: Session, ticker: str) -> models.Entity | None:
    return s.scalars(select(models.Entity).where(models.Entity.ticker == ticker.upper())).first()


def all_entities(s: Session) -> list[models.Entity]:
    return list(s.scalars(select(models.Entity)))


def get_source(s: Session, source_id: str) -> models.Source | None:
    return s.get(models.Source, source_id)


# ---------- snapshots & documents ----------


def snapshot_exists(s: Session, source_id: str, content_hash: str) -> bool:
    q = select(models.Snapshot.id).where(
        models.Snapshot.source_id == source_id,
        models.Snapshot.content_hash == content_hash,
        models.Snapshot.run_id == current_run_id.get(),
    )
    return s.scalars(q).first() is not None


def get_document(s: Session, doc_id: str) -> models.Document | None:
    return s.get(models.Document, doc_id)


def document_for_snapshot(s: Session, snapshot_id: str) -> models.Document | None:
    return s.scalars(select(models.Document).where(models.Document.snapshot_id == snapshot_id)).first()


# ---------- events ----------


def event_by_exact(s: Session, fp: str) -> models.Event | None:
    return s.scalars(select(models.Event).where(models.Event.exact_fingerprint == fp, models.Event.run_id == current_run_id.get())).first()


def events_by_structural(s: Session, fp: str, within_hours: int, before: datetime) -> list[models.Event]:
    since = before - timedelta(hours=within_hours)
    q = (
        select(models.Event)
        .where(
            models.Event.structural_fingerprint == fp,
            models.Event.run_id == current_run_id.get(),
            models.Event.first_detected_at >= since,
            models.Event.first_detected_at <= before,
        )
        .order_by(models.Event.first_detected_at)
    )
    return list(s.scalars(q))


def last_same_type_event(s: Session, entity_id: str, event_type: str, before: datetime, exclude_id: str | None = None) -> models.Event | None:
    q = (
        select(models.Event)
        .where(
            models.Event.primary_entity_id == entity_id,
            models.Event.event_type == event_type,
            models.Event.run_id == current_run_id.get(),
            models.Event.first_detected_at < before,
            models.Event.status != schemas.Status.duplicate.value,
        )
        .order_by(desc(models.Event.first_detected_at))
    )
    for row in s.scalars(q):
        if row.id != exclude_id:
            return row
    return None


def latest_reference_figure(s: Session, entity_id: str, field: str, before: datetime) -> tuple[dict, models.Event] | None:
    """Most recent earnings event for the entity that carries `field` (e.g. revenue)."""
    q = (
        select(models.Event)
        .where(
            models.Event.primary_entity_id == entity_id,
            models.Event.event_type == schemas.EventType.earnings.value,
            models.Event.run_id == current_run_id.get(),
            models.Event.first_detected_at < before,
        )
        .order_by(desc(models.Event.first_detected_at))
    )
    for row in s.scalars(q):
        val = (row.payload.get("fields") or {}).get(field)
        if val:
            return val, row
    return None


def recent_story_candidates(s: Session, entity_id: str | None, before: datetime, hours: int = 168) -> list[models.Event]:
    since = before - timedelta(hours=hours)
    q = select(models.Event).where(
        models.Event.run_id == current_run_id.get(),
        models.Event.first_detected_at >= since,
        models.Event.first_detected_at <= before,
        models.Event.story_id.is_not(None),
    )
    if entity_id:
        q = q.where(models.Event.primary_entity_id == entity_id)
    return list(s.scalars(q))


def upsert_event(s: Session, ev: schemas.Event) -> models.Event:
    row = s.get(models.Event, ev.id) or models.Event(id=ev.id)
    row.exact_fingerprint = ev.exact_fingerprint
    row.structural_fingerprint = ev.structural_fingerprint
    row.event_type = ev.event_type.value
    row.market = ev.market
    row.source_id = ev.source_id
    row.primary_entity_id = ev.primary_entity.id
    row.occurred_at = ev.occurred_at
    row.source_published_at = ev.source_published_at
    row.first_detected_at = ev.first_detected_at
    row.story_id = ev.story_id
    row.status = ev.status.value
    row.materiality = ev.materiality
    row.payload = ev.model_dump(mode="json")
    row.schema_version = ev.schema_version
    row.run_id = current_run_id.get()
    s.merge(row)
    return row


def replace_claims(s: Session, event_id: str, claims: list[schemas.Claim]) -> None:
    for old in s.scalars(select(models.Claim).where(models.Claim.event_id == event_id)):
        s.delete(old)
    s.flush()
    for c in claims:
        s.add(
            models.Claim(
                id=c.id,
                event_id=event_id,
                field=c.field,
                value={"v": c.value},
                unit=c.unit,
                evidence_doc_id=c.evidence_doc_id,
                span_start=c.span.start,
                span_end=c.span.end,
                span_text=c.span_text,
                extractor=c.extractor,
                check_status=c.check_status.value,
                checked_by=c.checked_by,
                reason=c.reason,
            )
        )


def claims_for(s: Session, event_id: str) -> list[models.Claim]:
    return list(s.scalars(select(models.Claim).where(models.Claim.event_id == event_id).order_by(models.Claim.field)))


def record_decision(s: Session, event_id: str, stage: str, outcome: str, reason: str, rule_id: str | None = None):
    s.add(
        models.Decision(
            event_id=event_id,
            stage=stage,
            outcome=outcome,
            reason=reason,
            rule_id=rule_id,
            created_at=clock.now(),
            run_id=current_run_id.get(),
        )
    )


def decisions_for(s: Session, event_id: str) -> list[models.Decision]:
    return list(s.scalars(select(models.Decision).where(models.Decision.event_id == event_id).order_by(models.Decision.id)))


def add_prior_link(s: Session, event_id: str, prior_id: str, relation: str, diff: dict | None = None):
    s.merge(models.PriorLink(event_id=event_id, prior_event_id=prior_id, relation=relation, diff=diff or {}))


# ---------- stories ----------


def get_or_create_story(s: Session, event: schemas.Event, title: str, shingles: list[str]) -> models.Story:
    sid = derive("sty", event.id)
    row = s.get(models.Story, sid)
    if not row:
        row = models.Story(id=sid, title=title, first_event_id=event.id, last_updated_at=clock.now(), shingles=shingles)
        s.add(row)
    return row


def touch_story(s: Session, story_id: str) -> None:
    row = s.get(models.Story, story_id)
    if row:
        row.last_updated_at = clock.now()


# ---------- alerts ----------


def latest_alert_for_event(s: Session, event_id: str) -> models.Alert | None:
    q = select(models.Alert).where(models.Alert.event_id == event_id, models.Alert.run_id == current_run_id.get()).order_by(desc(models.Alert.version))
    return s.scalars(q).first()


def save_alert(s: Session, alert: schemas.Alert) -> models.Alert:
    row = models.Alert(
        id=f"{alert.alert_id}:v{alert.version}:{current_run_id.get()}",
        alert_id=alert.alert_id,
        version=alert.version,
        event_id=alert.event_id,
        story_id=alert.story_id,
        status=alert.status.value,
        market=alert.market,
        event_type=alert.event_type.value,
        primary_entity_id=alert.entity.id,
        headline=alert.headline,
        body=alert.model_dump(mode="json"),
        materiality=alert.materiality,
        published_at=alert.timestamps.published,
        correction_of_alert_id=alert.correction_of,
        run_id=current_run_id.get(),
    )
    s.add(row)
    return row


def list_alerts(
    s: Session,
    *,
    market: str | None = None,
    entity_id: str | None = None,
    event_type: str | None = None,
    min_materiality: float | None = None,
    since: datetime | None = None,
    limit: int = 50,
    run_id: str | None = None,
) -> list[models.Alert]:
    q = select(models.Alert).where(models.Alert.run_id == (run_id or current_run_id.get()))
    if market:
        q = q.where(models.Alert.market == market)
    if entity_id:
        q = q.where(models.Alert.primary_entity_id == entity_id)
    if event_type:
        q = q.where(models.Alert.event_type == event_type)
    if min_materiality is not None:
        q = q.where(models.Alert.materiality >= min_materiality)
    if since:
        q = q.where(models.Alert.published_at >= since)
    q = q.order_by(desc(models.Alert.published_at), desc(models.Alert.version)).limit(limit)
    return list(s.scalars(q))


def alert_versions(s: Session, alert_id: str) -> list[models.Alert]:
    q = select(models.Alert).where(models.Alert.alert_id == alert_id, models.Alert.run_id == current_run_id.get()).order_by(models.Alert.version)
    return list(s.scalars(q))


# ---------- review ----------


def enqueue_review(s: Session, event_id: str, reason: str) -> models.ReviewItem:
    rid = derive("rev", event_id, current_run_id.get())
    row = s.get(models.ReviewItem, rid)
    if not row:
        row = models.ReviewItem(id=rid, event_id=event_id, reason=reason, created_at=clock.now(), run_id=current_run_id.get())
        s.add(row)
    return row
