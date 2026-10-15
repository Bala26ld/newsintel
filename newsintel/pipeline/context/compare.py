"""Prior-disclosure comparison and reference figures.

`what changed` is a mechanical diff of structured fields against the last event of the same type
for the same entity. Reference figures (last reported revenue) are copied as claims with the
original evidence span, so a ratio in the alert is itself traceable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from newsintel.core.ids import derive
from newsintel.core.schemas import ALWAYS_NOT_ESTABLISHED, FIELD_LABELS, CheckStatus, Claim, Event, EventType, Span
from newsintel.store import models, repo


@dataclass
class Reference:
    field: str  # e.g. "revenue"
    amount: int
    currency: str
    as_stated: str
    period_label: str | None
    annualized: int
    claim: Claim
    source_event_id: str


@dataclass
class ContextResult:
    prior_event_id: str | None = None
    prior_fields: dict = field(default_factory=dict)
    diff: dict = field(default_factory=dict)  # field -> {"prior":..., "now":...}
    reference: Reference | None = None
    amount_to_revenue: float | None = None
    not_established: list[str] = field(default_factory=list)
    reference_claims: list[Claim] = field(default_factory=list)


def _flat(v):
    return (v["amount"], v["currency"]) if isinstance(v, dict) and "amount" in v else v


def primary_amount(ev: Event) -> int | None:
    for key in ("contract_value", "consideration", "revenue", "penalty"):
        v = ev.fields.get(key)
        if isinstance(v, dict) and v.get("amount"):
            return int(v["amount"])
    return None


def build_context(s: Session, ev: Event) -> ContextResult:
    res = ContextResult()
    now = ev.first_detected_at
    if ev.primary_entity.id:
        prior = repo.last_same_type_event(s, ev.primary_entity.id, ev.event_type.value, now, exclude_id=ev.id)
        if prior:
            res.prior_event_id = prior.id
            res.prior_fields = prior.payload.get("fields") or {}
            for k in sorted(set(res.prior_fields) | set(ev.fields)):
                if k == "description":
                    continue
                a, b = _flat(res.prior_fields.get(k)), _flat(ev.fields.get(k))
                if a != b:
                    res.diff[k] = {"prior": res.prior_fields.get(k), "now": ev.fields.get(k)}
        # reference figure: last reported revenue (from an earnings event, excluding this one)
        if ev.event_type != EventType.earnings or True:
            ref = repo.latest_reference_figure(s, ev.primary_entity.id, "revenue", now)
            if ref and ref[1].id != ev.id:
                val, src = ref
                res.reference = _reference_from(s, ev, val, src)
                if res.reference:
                    res.reference_claims.append(res.reference.claim)
                    amt = primary_amount(ev)
                    if amt and ev.event_type != EventType.earnings and res.reference.annualized:
                        res.amount_to_revenue = amt / res.reference.annualized
    labels = [FIELD_LABELS.get(f, f) for f in ev.empty_fields()]
    res.not_established = labels + [x for x in ALWAYS_NOT_ESTABLISHED if ev.event_type != EventType.macro_release]
    return res


def _reference_from(s: Session, ev: Event, val: dict, src: models.Event) -> Reference | None:
    src_claims = {c.field: c for c in repo.claims_for(s, src.id)}
    amount_claim = src_claims.get("revenue.amount")
    if not amount_claim or amount_claim.check_status != CheckStatus.supported.value:
        return None
    period_label = (src.payload.get("fields") or {}).get("period_label")
    annualized = int(val["amount"]) * (4 if period_label and "quarter" in period_label.lower() else 1)
    if period_label and ("months" in period_label.lower()):
        annualized = int(val["amount"]) * (12 // _months(period_label))
    claim = Claim(
        id=derive("clm", ev.id, "reference.revenue", amount_claim.span_text, str(amount_claim.span_start)),
        event_id=ev.id,
        field="reference.revenue",
        value=int(val["amount"]),
        unit=val["currency"],
        evidence_doc_id=amount_claim.evidence_doc_id,
        span=Span(start=amount_claim.span_start, end=amount_claim.span_end),
        span_text=amount_claim.span_text,
        extractor=f"reference:{src.id}",
        check_status=CheckStatus.supported,
        checked_by="inherited:" + (amount_claim.checked_by or "rule"),
    )
    return Reference(
        field="revenue",
        amount=int(val["amount"]),
        currency=val["currency"],
        as_stated=val.get("as_stated", ""),
        period_label=period_label,
        annualized=annualized,
        claim=claim,
        source_event_id=src.id,
    )


def _months(label: str) -> int:
    for word, n in (("three", 3), ("six", 6), ("nine", 9), ("twelve", 12)):
        if word in label.lower():
            return n
    return 12


def period_date(ev: Event) -> date | None:
    v = ev.fields.get("period_end")
    return date.fromisoformat(v) if v else None
