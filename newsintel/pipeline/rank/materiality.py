"""Materiality score: weighted sum of five sub-scores, no model call, from config/policy.yaml."""

from __future__ import annotations

from newsintel.core.config import policy
from newsintel.core.schemas import Event, EventType
from newsintel.pipeline.context.compare import ContextResult


def _curve(x: float, points: list[list[float]]) -> float:
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
    return pts[-1][1]


def significance(ev: Event, ctx: ContextResult, cfg: dict) -> float:
    if ctx.amount_to_revenue is not None:
        return _curve(ctx.amount_to_revenue, cfg["significance_curve"])
    if ev.event_type == EventType.earnings and ctx.reference and ev.fields.get("revenue"):
        # earnings: significance from the size of the revenue change versus the prior report
        now_amt = ev.fields["revenue"]["amount"]
        prior = ctx.prior_fields.get("revenue", {}).get("amount") if ctx.prior_fields else None
        if prior:
            return min(1.0, 0.4 + abs(now_amt - prior) / prior * 2)
    return float(cfg["type_prior"].get(ev.event_type.value, 0.5))


def unexpectedness(ev: Event, ctx: ContextResult) -> float:
    if ctx.prior_event_id is None:
        return 0.8  # first event of this type recorded for the entity
    if not ctx.diff:
        return 0.0  # exact repeat
    if ev.event_type == EventType.earnings:
        prior = ctx.prior_fields.get("revenue", {}).get("amount") if ctx.prior_fields else None
        now_amt = (ev.fields.get("revenue") or {}).get("amount")
        if prior and now_amt:
            return min(1.0, abs(now_amt - prior) / prior * 4)
        guided = (ctx.prior_fields.get("guidance_revenue") or {}).get("amount")
        if guided and now_amt:
            return min(1.0, abs(now_amt - guided) / guided * 5)
    return 0.5


def breadth(ev: Event, cfg: dict) -> float:
    n = 1 + len([r for r in ev.related_entities if r.name])
    floor = float(cfg["breadth_floor"].get(ev.event_type.value, 0.0))
    return max(floor, min(n, 5) / 5)


def freshness(dedupe_outcome: str) -> float:
    return {"new": 1.0, "story_join": 0.5, "correction": 0.5}.get(dedupe_outcome, 0.0)


def score(ev: Event, ctx: ContextResult, source_trust: float, dedupe_outcome: str) -> tuple[float, dict[str, float]]:
    cfg = policy()["materiality"]
    w = cfg["weights"]
    parts = {
        "significance": round(significance(ev, ctx, cfg), 3),
        "unexpectedness": round(unexpectedness(ev, ctx), 3),
        "source_reliability": round(max(0.0, min(1.0, source_trust)), 3),
        "breadth": round(breadth(ev, cfg), 3),
        "freshness": round(freshness(dedupe_outcome), 3),
    }
    total = sum(w[k] * parts[k] for k in parts)
    return round(total, 3), parts


def band(m: float) -> str:
    b = policy()["materiality"]["bands"]
    return "high" if m >= b["high"] else "medium" if m >= b["medium"] else "low"
