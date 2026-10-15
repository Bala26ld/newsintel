"""ORM models: the fourteen tables from the design doc.

Rows in `alerts`, `decisions`, `snapshots` and `model_runs` are append-only. Nothing in the
pipeline updates a published alert in place; a correction is a new version row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(32))
    access_method: Mapped[str] = mapped_column(String(32))
    poll_interval_s: Mapped[int] = mapped_column(Integer, default=60)
    terms_url: Mapped[str | None] = mapped_column(String(512))
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    may_store_full_text: Mapped[bool] = mapped_column(Boolean, default=False)
    may_redistribute_text: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    # rolling health, updated by the scheduler
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    corrections: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[int] = mapped_column(Integer, default=0)


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(256))
    country: Mapped[str] = mapped_column(String(8))
    exchange: Mapped[str | None] = mapped_column(String(16))
    ticker: Mapped[str | None] = mapped_column(String(16), index=True)
    isin: Mapped[str | None] = mapped_column(String(16))
    lei: Mapped[str | None] = mapped_column(String(24))
    cik: Mapped[str | None] = mapped_column(String(12), index=True)
    bse_code: Mapped[str | None] = mapped_column(String(12))
    parent_id: Mapped[str | None] = mapped_column(String(64))
    aliases: Mapped[list] = mapped_column(JSONType, default=list)
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, default=1)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(80), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_key: Mapped[str] = mapped_column(String(512))
    http_meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    run_id: Mapped[str] = mapped_column(String(64), default="live", index=True)
    __table_args__ = (UniqueConstraint("source_id", "content_hash", "run_id", name="uq_snapshot_source_hash_run"),)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), ForeignKey("snapshots.id"), index=True)
    mime: Mapped[str] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)  # null when the source forbids storing text
    text_hash: Mapped[str] = mapped_column(String(80))
    parse_method: Mapped[str] = mapped_column(String(32))
    page_map: Mapped[dict] = mapped_column(JSONType, default=dict)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)  # form type, items, registrant, cik...


class Story(Base):
    __tablename__ = "stories"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    first_event_id: Mapped[str] = mapped_column(String(64))
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    shingles: Mapped[list] = mapped_column(JSONType, default=list)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exact_fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    structural_fingerprint: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(8))
    source_id: Mapped[str] = mapped_column(String(64))
    primary_entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    story_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    materiality: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONType)  # full Event schema
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    run_id: Mapped[str] = mapped_column(String(64), default="live", index=True)
    __table_args__ = (Index("ix_events_entity_type_time", "primary_entity_id", "event_type", "occurred_at"),)


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), ForeignKey("events.id"), index=True)
    field: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict] = mapped_column(JSONType)  # {"v": ...} wrapper so scalars store cleanly
    unit: Mapped[str | None] = mapped_column(String(16))
    evidence_doc_id: Mapped[str] = mapped_column(String(64))
    span_start: Mapped[int] = mapped_column(Integer)
    span_end: Mapped[int] = mapped_column(Integer)
    span_text: Mapped[str] = mapped_column(Text)
    extractor: Mapped[str] = mapped_column(String(128))
    check_status: Mapped[str] = mapped_column(String(16), default="pending")
    checked_by: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)


class PriorLink(Base):
    __tablename__ = "prior_links"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prior_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    relation: Mapped[str] = mapped_column(String(16))  # repeat | update | correction | follow_up | prior_same_type
    diff: Mapped[dict] = mapped_column(JSONType, default=dict)


class ModelRun(Base):
    __tablename__ = "model_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_hash: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    output: Mapped[dict] = mapped_column(JSONType)
    validated: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # alert_id + version
    alert_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    story_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    market: Mapped[str] = mapped_column(String(8), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    primary_entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    headline: Mapped[str] = mapped_column(String(512))
    body: Mapped[dict] = mapped_column(JSONType)  # full Alert schema
    materiality: Mapped[float] = mapped_column(Float)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    supersedes_alert_id: Mapped[str | None] = mapped_column(String(64))
    correction_of_alert_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64), default="live", index=True)
    __table_args__ = (UniqueConstraint("alert_id", "version", "run_id", name="uq_alert_version_run"),)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str] = mapped_column(String(64), default="live", index=True)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    entity_ids: Mapped[list] = mapped_column(JSONType, default=list)
    event_types: Mapped[list] = mapped_column(JSONType, default=list)
    markets: Mapped[list] = mapped_column(JSONType, default=list)
    min_band: Mapped[str] = mapped_column(String(8), default="low")


class ReviewItem(Base):
    __tablename__ = "review_queue"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(String(16))  # approve | reject
    decided_by: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(64), default="live", index=True)


class ExpectedEvent(Base):
    __tablename__ = "expected_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_s: Mapped[int] = mapped_column(Integer, default=3600)
    prior_values: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | arrived | missed
    arrived_event_id: Mapped[str | None] = mapped_column(String(64))
