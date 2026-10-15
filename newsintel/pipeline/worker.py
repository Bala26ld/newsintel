"""Pipeline worker: consumes snapshot.created and runs the stage chain.

MVP topology: one worker runs every stage for a snapshot in sequence; each stage is a pure
function that records its decision. Splitting stages across workers later means publishing the
intermediate event on the bus between two of these calls; nothing inside the stages changes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from newsintel.bus.base import ALERT_CORRECTED, ALERT_PUBLISHED, REVIEW_REQUESTED, SNAPSHOT_CREATED, STORY_UPDATED, Bus
from newsintel.core import clock
from newsintel.core.config import sources
from newsintel.core.fingerprints import structural_fingerprint
from newsintel.core.ids import derive
from newsintel.core.schemas import EntityRef, Event, EventType, Evidence, GateOutcome, Status
from newsintel.ingest.snapshot_store import LocalObjectStore
from newsintel.pipeline.context.compare import ContextResult, build_context, period_date, primary_amount
from newsintel.pipeline.dedupe.cluster import dedupe
from newsintel.pipeline.extract.text import parse_8k_meta, parse_document
from newsintel.pipeline.extract.tier0 import extract_8k
from newsintel.pipeline.gate.policy import evaluate
from newsintel.pipeline.publish.alerts import AlertRejected, build_alert
from newsintel.pipeline.rank.materiality import score
from newsintel.pipeline.resolve.registry import resolve_primary, resolve_related
from newsintel.pipeline.verify.checks import all_supported, materialize, verify_claims
from newsintel.store import models, repo
from newsintel.store.db import current_run_id, session

log = logging.getLogger(__name__)

OFFICIAL_KINDS = {"exchange", "regulator", "central_bank", "statistics", "government", "company_ir"}


@dataclass
class Outcome:
    snapshot_id: str
    event_id: str | None
    stage: str
    result: str
    alert_id: str | None = None


class PipelineWorker:
    def __init__(self, bus: Bus, store: LocalObjectStore | None = None):
        self.bus = bus
        self.store = store or LocalObjectStore()
        self._source_cfg = {s["id"]: s for s in sources()}

    async def run(self, stop: asyncio.Event, group: str = "pipeline") -> None:
        async for msg in self.bus.subscribe(SNAPSHOT_CREATED, group):
            if stop.is_set():
                break
            try:
                await self.process(msg.payload["snapshot_id"])
                await self.bus.ack(msg, group)
            except Exception as exc:  # noqa: BLE001
                log.exception("pipeline failed for %s", msg.key)
                await self.bus.fail(msg, group, repr(exc))

    # ------------------------------------------------------------------ stages

    async def process(self, snapshot_id: str) -> Outcome:
        with session() as s:
            snap = s.get(models.Snapshot, snapshot_id)
            if snap is None:
                return Outcome(snapshot_id, None, "load", "snapshot_missing")
            if repo.document_for_snapshot(s, snapshot_id):
                return Outcome(snapshot_id, None, "load", "already_processed")  # idempotent redelivery
            source = repo.get_source(s, snap.source_id)
            src_cfg = self._source_cfg.get(snap.source_id, {})
            body = self.store.get(snap.storage_key)
            hints = (snap.http_meta or {}).get("meta", {})

            # ---- Extract (document) ----
            parsed = parse_document(body, (snap.http_meta or {}).get("mime", "text/html"))
            meta = {**hints, **parse_8k_meta(parsed.text)}
            if hints.get("items"):
                meta["items"] = sorted(set(meta.get("items", [])) | set(hints["items"]))
            doc_id = derive("doc", snapshot_id)
            may_store = bool(source and source.may_store_full_text)
            s.add(
                models.Document(
                    id=doc_id,
                    snapshot_id=snapshot_id,
                    mime=parsed.mime,
                    text=parsed.text if may_store else None,
                    text_hash=derive("txt", parsed.text, length=40),
                    parse_method=parsed.parse_method,
                    meta=meta,
                )
            )
            s.flush()

            # ---- Extract (fields + claims) ----
            form = str(meta.get("form", "8-K"))
            result = extract_8k(parsed.text, doc_id, meta.get("items", [])) if form.startswith("8-K") else None
            event_id = derive("evt", current_run_id.get(), snap.source_id, snap.content_hash)
            if result is None:
                repo.record_decision(s, event_id, "extract", "no_event", f"no supported event in items {meta.get('items')}", "X0")
                return Outcome(snapshot_id, event_id, "extract", "no_event")

            # ---- Resolve ----
            tickers = meta.get("tickers") or []
            primary, flags = resolve_primary(s, cik=meta.get("cik"), ticker=tickers[0] if tickers else None, name=meta.get("registrant"))
            related = resolve_related(s, result.related)
            flags = list(dict.fromkeys(flags + result.flags))

            occurred = result.occurred_on or (datetime.fromisoformat(meta["period_of_report"]).date() if meta.get("period_of_report") else None)
            ev = Event(
                id=event_id,
                event_type=result.event_type,
                market=src_cfg.get("market", source.market if source else "US"),
                source_id=snap.source_id,
                primary_entity=primary,
                related_entities=related,
                occurred_at=datetime.combine(occurred, datetime.min.time(), tzinfo=snap.retrieved_at.tzinfo) if occurred else None,
                source_published_at=snap.source_published_at,
                first_detected_at=snap.retrieved_at,
                fields=result.fields,
                evidence=[Evidence(doc_id=doc_id, url=snap.url, hash=snap.content_hash, label=f"{form} filing", source_id=snap.source_id, published_at=snap.source_published_at)],
                exact_fingerprint=snap.content_hash,
                structural_fingerprint="",
                ambiguity_flags=flags,
            )
            ev.structural_fingerprint = structural_fingerprint(
                ev.event_type.value, ev.primary_entity.id, primary_amount(ev), period_date(ev) if ev.event_type in {EventType.earnings, EventType.macro_release} else None
            )
            repo.record_decision(s, ev.id, "extract", "ok", f"{result.extractor}: {len(result.claims)} claims, type {ev.event_type.value}", "X1")
            repo.record_decision(s, ev.id, "resolve", "ok" if primary.id else "unresolved", f"primary={primary.id or primary.name}; flags={flags}", "R1")

            # ---- Dedupe / cluster ----
            dd = dedupe(s, ev, parsed.text[result.section[0] : result.section[1]], form=form)
            repo.record_decision(s, ev.id, "dedupe", dd.outcome, f"prior={dd.prior_event_id} story={dd.story_id} changed={dd.changed_fields}", "D1")
            if dd.outcome == "exact_duplicate":
                return Outcome(snapshot_id, ev.id, "dedupe", "exact_duplicate")
            if dd.outcome in {"structural_duplicate", "story_join", "correction"} and dd.story_id:
                ev.story_id = dd.story_id
                repo.touch_story(s, dd.story_id)
            else:
                story = repo.get_or_create_story(s, ev, title=ev.primary_entity.name + ": " + ev.event_type.value.replace("_", " "), shingles=dd.shingles)
                ev.story_id = story.id
            if dd.prior_event_id:
                repo.add_prior_link(s, ev.id, dd.prior_event_id, "repeat" if dd.outcome == "structural_duplicate" else dd.outcome, {"changed": dd.changed_fields})
            if dd.outcome == "structural_duplicate":
                ev.status = Status.duplicate
                repo.upsert_event(s, ev)
                repo.replace_claims(s, ev.id, materialize(ev, result.claims))
                repo.record_decision(s, ev.id, "gate", GateOutcome.merge.value, f"merged into story {ev.story_id}", "G3")
                await self.bus.publish(STORY_UPDATED, key=f"{ev.story_id}:{ev.id}", payload={"story_id": ev.story_id, "event_id": ev.id, "merged": True})
                return Outcome(snapshot_id, ev.id, "gate", "merge")

            # ---- Verify ----
            claims = materialize(ev, result.claims)
            domain_ok = _domain_allowed(snap.url, src_cfg.get("allowed_domains"))
            claims, vflags = verify_claims(ev, claims, parsed.text, domain_ok)
            ev.ambiguity_flags = list(dict.fromkeys(ev.ambiguity_flags + vflags))
            official = bool(source and source.kind in OFFICIAL_KINDS)
            if all_supported(claims) and official and ev.primary_entity.id:
                ev.status = Status.corrected if dd.outcome == "correction" else Status.verified_official
            elif all_supported(claims) and not official:
                ev.status = Status.corroborated_report
            else:
                ev.status = Status.developing
            repo.record_decision(s, ev.id, "verify", ev.status.value, f"{sum(c.check_status.value == 'supported' for c in claims)}/{len(claims)} claims supported; flags={vflags}", "V1")

            # ---- Context ----
            ctx: ContextResult = build_context(s, ev)
            claims = claims + ctx.reference_claims
            if ctx.prior_event_id and dd.outcome != "correction":
                repo.add_prior_link(s, ev.id, ctx.prior_event_id, "prior_same_type", ctx.diff)
            repo.record_decision(s, ev.id, "context", "ok", f"prior={ctx.prior_event_id} diff={sorted(ctx.diff)} ratio={ctx.amount_to_revenue}", "C1")

            # ---- Rank ----
            trust = float(source.trust_score) if source else 0.5
            ev.materiality, ev.materiality_parts = score(ev, ctx, trust, dd.outcome)
            repo.record_decision(s, ev.id, "rank", f"{ev.materiality:.3f}", str(ev.materiality_parts), "M1")

            # ---- Gate ----
            decision = evaluate(ev, claims, ctx, dd.outcome, trust)
            repo.upsert_event(s, ev)
            repo.replace_claims(s, ev.id, claims)
            repo.record_decision(s, ev.id, "gate", decision.outcome.value, decision.reason, decision.rule_id)
            if decision.outcome == GateOutcome.hold_review:
                repo.enqueue_review(s, ev.id, decision.reason)
                await self.bus.publish(REVIEW_REQUESTED, key=ev.id, payload={"event_id": ev.id, "reason": decision.reason})
                return Outcome(snapshot_id, ev.id, "gate", "hold_review")
            if decision.outcome != GateOutcome.publish:
                return Outcome(snapshot_id, ev.id, "gate", decision.outcome.value)

            # ---- Publish ----
            try:
                alert, prior_claims = build_alert(s, ev, claims, ctx, dd.outcome, label=decision.label)
            except AlertRejected as exc:
                repo.record_decision(s, ev.id, "publish", "rejected", str(exc), "P0")
                return Outcome(snapshot_id, ev.id, "publish", "rejected")
            if prior_claims:
                repo.replace_claims(s, ev.id, claims + prior_claims)
            row = repo.save_alert(s, alert)
            if source:
                source.published += 1
                if alert.correction_of:
                    source.corrections += 1
            repo.record_decision(s, ev.id, "publish", "published", f"{alert.alert_id} v{alert.version}", "P1")
            s.flush()
            payload = {
                "alert_id": alert.alert_id,
                "version": alert.version,
                "event_id": ev.id,
                "headline": alert.headline,
                "market": alert.market,
                "event_type": alert.event_type.value,
                "entity_id": ev.primary_entity.id,
                "band": alert.materiality_band,
                "published_at": clock.iso(alert.timestamps.published),
                "row_id": row.id,
            }
            await self.bus.publish(ALERT_CORRECTED if alert.correction_of else ALERT_PUBLISHED, key=row.id, payload=payload)
            await self.bus.publish(STORY_UPDATED, key=f"{ev.story_id}:{ev.id}", payload={"story_id": ev.story_id, "event_id": ev.id, "merged": False})
            return Outcome(snapshot_id, ev.id, "publish", "published", alert_id=row.id)


def _domain_allowed(url: str, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    host = (urlparse(url).hostname or "").lower()
    return host in {d.lower() for d in allowed}


def unresolved(ref: EntityRef) -> bool:
    return ref.id is None
