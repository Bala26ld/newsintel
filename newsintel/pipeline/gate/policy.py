"""Publication gate: the rules from the design doc, evaluated in order; the first match decides."""

from __future__ import annotations

from dataclasses import dataclass

from newsintel.core.config import policy
from newsintel.core.schemas import CheckStatus, Claim, Event, GateOutcome, Status
from newsintel.pipeline.context.compare import ContextResult


@dataclass
class GateDecision:
    outcome: GateOutcome
    rule_id: str
    reason: str
    label: str | None = None


def evaluate(ev: Event, claims: list[Claim], ctx: ContextResult, dedupe_outcome: str, source_trust: float, tier2_contradiction: bool = False) -> GateDecision:
    cfg = policy()["gate"]
    unsupported = [c for c in claims if c.check_status == CheckStatus.unsupported]

    # 1. unsupported claim -> hold
    if unsupported:
        fields = ", ".join(sorted({c.field for c in unsupported}))
        return GateDecision(GateOutcome.hold_developing, "G1", f"unsupported claims: {fields}")
    # 2. primary entity unresolved -> review
    if ev.primary_entity.id is None:
        return GateDecision(GateOutcome.hold_review, "G2", "primary entity unresolved")
    # 3. duplicate -> merge, no alert
    if dedupe_outcome in {"exact_duplicate", "structural_duplicate"}:
        return GateDecision(GateOutcome.merge, "G3", f"{dedupe_outcome} within {cfg['duplicate_window_hours']} h")
    # 6 (checked before 4 so a high-impact verified event can still be held): review conditions
    m = ev.materiality or 0.0
    if m >= float(cfg["review_materiality"]):
        if tier2_contradiction:
            return GateDecision(GateOutcome.hold_review, "G6a", "tier-2 contradiction found")
        if source_trust < float(cfg["review_low_trust"]):
            return GateDecision(GateOutcome.hold_review, "G6b", f"source trust {source_trust:.2f} below {cfg['review_low_trust']}")
    # 6c stands alone: an amount above the revenue share threshold is reviewed whatever the score says
    if ctx.amount_to_revenue is not None and ctx.amount_to_revenue > float(cfg["review_amount_to_revenue"]):
        return GateDecision(GateOutcome.hold_review, "G6c", f"amount is {ctx.amount_to_revenue:.0%} of last annualised revenue")
    # 4. verified official -> publish
    if ev.status in {Status.verified_official, Status.corrected}:
        return GateDecision(GateOutcome.publish, "G4", "verified official source, all claims supported")
    # 5. corroborated report under policy
    if ev.status == Status.corroborated_report:
        rp = policy()["reporting_policy"].get(ev.market, {})
        if rp.get("enabled") and ev.event_type.value in rp.get("event_types", []) and source_trust >= float(cfg["corroborated_min_trust"]):
            return GateDecision(GateOutcome.publish, "G5", "corroborated report under reporting policy", label=f"Reported by {ev.source_id}; official confirmation pending")
        return GateDecision(GateOutcome.hold_developing, "G5", "corroborated report but reporting policy disabled for this market/type")
    # 7. otherwise hold
    return GateDecision(GateOutcome.hold_developing, "G7", f"status {ev.status.value}; re-evaluate in {cfg['reevaluate_after_minutes']} min")
