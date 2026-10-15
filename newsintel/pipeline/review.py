"""Human review decisions re-enter the gate; they never publish directly."""

from __future__ import annotations

from newsintel.bus.base import ALERT_PUBLISHED, Bus
from newsintel.core import clock
from newsintel.core.schemas import CheckStatus, Claim, Event, Span, Status
from newsintel.pipeline.context.compare import build_context
from newsintel.pipeline.publish.alerts import AlertRejected, build_alert
from newsintel.store import models, repo
from newsintel.store.db import session


def _claims_from_rows(rows: list[models.Claim]) -> list[Claim]:
    return [
        Claim(
            id=r.id,
            event_id=r.event_id,
            field=r.field,
            value=r.value.get("v"),
            unit=r.unit,
            evidence_doc_id=r.evidence_doc_id,
            span=Span(start=r.span_start, end=r.span_end),
            span_text=r.span_text,
            extractor=r.extractor,
            check_status=CheckStatus(r.check_status),
            checked_by=r.checked_by,
            reason=r.reason,
        )
        for r in rows
    ]


async def decide(bus: Bus, review_id: str, decision: str, reviewer: str, note: str | None = None) -> dict:
    with session() as s:
        item = s.get(models.ReviewItem, review_id)
        if item is None:
            raise KeyError(review_id)
        if item.decided_at is not None:
            return {"review_id": review_id, "status": "already_decided", "decision": item.decision}
        item.decided_at = clock.now()
        item.decision = decision
        item.decided_by = reviewer
        item.note = note
        ev_row = s.get(models.Event, item.event_id)
        ev = Event.model_validate(ev_row.payload)
        if decision != "approve":
            ev.status = Status.withdrawn
            repo.upsert_event(s, ev)
            repo.record_decision(s, ev.id, "review", "rejected", f"by {reviewer}: {note or ''}", "H1")
            return {"review_id": review_id, "status": "rejected"}
        # Approved: an approved item re-enters the gate with the review condition satisfied.
        claims = _claims_from_rows(repo.claims_for(s, ev.id))
        if any(c.check_status != CheckStatus.supported for c in claims):
            repo.record_decision(s, ev.id, "review", "blocked", "approved but unsupported claims remain", "H2")
            return {"review_id": review_id, "status": "blocked_unsupported_claims"}
        ctx = build_context(s, ev)
        if ev.status == Status.developing:
            ev.status = Status.verified_official
        repo.record_decision(s, ev.id, "review", "approved", f"by {reviewer}: {note or ''}", "H1")
        try:
            alert, prior_claims = build_alert(s, ev, claims, ctx, "new", label="Published after editorial review")
        except AlertRejected as exc:
            repo.record_decision(s, ev.id, "publish", "rejected", str(exc), "P0")
            return {"review_id": review_id, "status": "rejected_by_validator", "reason": str(exc)}
        row = repo.save_alert(s, alert)
        repo.upsert_event(s, ev)
        repo.record_decision(s, ev.id, "publish", "published", f"{alert.alert_id} v{alert.version} (after review)", "P1")
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
    await bus.publish(ALERT_PUBLISHED, key=row.id, payload=payload)
    return {"review_id": review_id, "status": "published", "alert": payload}
