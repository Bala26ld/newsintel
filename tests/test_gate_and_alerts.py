from datetime import UTC, datetime

from newsintel.core.schemas import CheckStatus, Claim, EntityRef, Event, EventType, GateOutcome, Sentence, Span, Status
from newsintel.pipeline.context.compare import ContextResult
from newsintel.pipeline.gate.policy import evaluate
from newsintel.pipeline.publish.alerts import validate_sentences


def _event(status=Status.verified_official, entity="e1", materiality=0.5):
    return Event(
        id="evt_x",
        event_type=EventType.contract_award,
        market="US",
        source_id="sec_edgar",
        primary_entity=EntityRef(id=entity, name="X Corp"),
        first_detected_at=datetime(2026, 10, 15, tzinfo=UTC),
        exact_fingerprint="sha256:a",
        structural_fingerprint="sf_a",
        status=status,
        materiality=materiality,
    )


def _claim(status=CheckStatus.supported, field="contract_value.amount"):
    return Claim(id="c1", event_id="evt_x", field=field, value=1, evidence_doc_id="d", span=Span(start=0, end=1), span_text="x", extractor="rule:t", check_status=status)


def test_unsupported_claim_holds():
    d = evaluate(_event(), [_claim(CheckStatus.unsupported)], ContextResult(), "new", 1.0)
    assert d.outcome == GateOutcome.hold_developing and d.rule_id == "G1"


def test_unresolved_entity_goes_to_review():
    d = evaluate(_event(entity=None), [_claim()], ContextResult(), "new", 1.0)
    assert d.outcome == GateOutcome.hold_review and d.rule_id == "G2"


def test_duplicate_merges():
    assert evaluate(_event(), [_claim()], ContextResult(), "structural_duplicate", 1.0).outcome == GateOutcome.merge


def test_large_amount_reviewed_even_when_verified():
    ctx = ContextResult(amount_to_revenue=0.6)
    d = evaluate(_event(materiality=0.5), [_claim()], ctx, "new", 1.0)
    assert d.outcome == GateOutcome.hold_review and d.rule_id == "G6c"


def test_verified_publishes_and_corroborated_held_by_policy():
    assert evaluate(_event(), [_claim()], ContextResult(), "new", 1.0).outcome == GateOutcome.publish
    d = evaluate(_event(status=Status.corroborated_report), [_claim()], ContextResult(), "new", 0.9)
    assert d.outcome == GateOutcome.hold_developing and d.rule_id == "G5"


def test_validator_strips_uncited_and_advisory_sentences():
    sents = [Sentence(text="Fact.", claim_ids=["c1"]), Sentence(text="Guess.", claim_ids=[]), Sentence(text="Unsupported.", claim_ids=["c9"]), Sentence(text="You should buy this.", claim_ids=["c1"])]
    kept = validate_sentences(sents, {"c1"})
    assert [s.text for s in kept] == ["Fact."]
