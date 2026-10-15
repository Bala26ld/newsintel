"""Alert builder and validator.

The builder assembles an Alert from supported claims only; the validator strips any sentence that
cites nothing or cites an unsupported claim, rejects advisory language, and refuses an alert whose
confirmed-facts list ends up empty. A UI can only render what survives this.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from newsintel.core import clock
from newsintel.core.config import policy
from newsintel.core.ids import derive
from newsintel.core.schemas import Alert, AlertTimestamps, CheckStatus, Claim, Event, Sentence
from newsintel.pipeline.context.compare import ContextResult
from newsintel.pipeline.publish import summariser
from newsintel.pipeline.rank.materiality import band
from newsintel.store import repo


class AlertRejected(Exception):
    pass


def _check_advisory(text: str) -> str | None:
    padded = f" {text.lower()} "
    for phrase in policy()["advisory_blocklist"]:
        if phrase.lower() in padded:
            return phrase.strip()
    return None


def validate_sentences(sentences: list[Sentence], supported: set[str]) -> list[Sentence]:
    kept = []
    for s in sentences:
        if not s.claim_ids or not set(s.claim_ids) <= supported:
            continue
        if _check_advisory(s.text):
            continue
        kept.append(s)
    return kept


def build_alert(
    s: Session,
    ev: Event,
    claims: list[Claim],
    ctx: ContextResult,
    dedupe_outcome: str,
    label: str | None = None,
) -> tuple[Alert, list[Claim]]:
    supported = {c.id for c in claims if c.check_status == CheckStatus.supported}
    prior_claims: list[Claim] = []
    correction_of = None
    version = 1
    alert_id = derive("alr", ev.id)
    if dedupe_outcome == "correction" and ctx.prior_event_id:
        prior_alert = repo.latest_alert_for_event(s, ctx.prior_event_id)
        if prior_alert:
            alert_id = prior_alert.alert_id
            version = prior_alert.version + 1
            correction_of = prior_alert.id
        for pc in repo.claims_for(s, ctx.prior_event_id):
            if pc.check_status == CheckStatus.supported.value:
                inherited = Claim(
                    id=derive("clm", ev.id, "prior." + pc.field, pc.span_text, str(pc.span_start)),
                    event_id=ev.id,
                    field="prior." + pc.field,
                    value=pc.value.get("v"),
                    unit=pc.unit,
                    evidence_doc_id=pc.evidence_doc_id,
                    span=pc_span(pc),
                    span_text=pc.span_text,
                    extractor=f"prior:{ctx.prior_event_id}",
                    check_status=CheckStatus.supported,
                    checked_by="inherited",
                )
                prior_claims.append(inherited)
                supported.add(inherited.id)
    facts = validate_sentences(summariser.confirmed_facts(ev, claims), supported)
    if not facts:
        raise AlertRejected("no supported confirmed facts")
    new = summariser.what_is_new(ev, claims, ctx, dedupe_outcome, [c for c in prior_claims])
    new_list = validate_sentences([new], supported) if new else []
    why = validate_sentences(summariser.why_it_may_matter(ev, claims, ctx), supported)
    head = summariser.headline(ev, ctx)
    if (bad := _check_advisory(head)) is not None:
        raise AlertRejected(f"advisory language in headline: {bad}")
    m = ev.materiality or 0.0
    alert = Alert(
        alert_id=alert_id,
        version=version,
        event_id=ev.id,
        story_id=ev.story_id,
        status=ev.status,
        event_type=ev.event_type,
        market=ev.market,
        entity=ev.primary_entity,
        headline=head,
        confirmed_facts=facts,
        what_is_new=new_list[0] if new_list else None,
        why_it_may_matter=why,
        not_established=ctx.not_established,
        evidence=ev.evidence,
        timestamps=AlertTimestamps(source=ev.source_published_at, detected=ev.first_detected_at, published=clock.now()),
        materiality_band=band(m),
        materiality=m,
        correction_of=correction_of,
        policy_version=int(policy()["version"]),
        label=label,
    )
    return alert, prior_claims


def pc_span(pc):
    from newsintel.core.schemas import Span

    return Span(start=pc.span_start, end=pc.span_end)
