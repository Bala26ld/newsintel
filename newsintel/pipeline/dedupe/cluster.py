"""Deduplication and story clustering.

Outcomes, in the order they are tested:
  exact_duplicate      same normalized bytes seen before (belt and braces; ingest already drops these)
  correction           same entity, type and reporting period (or an amended form on the same date)
                       as a verified event, with at least one changed field
  structural_duplicate same type + entity + rounded amount (+ period) inside the policy window
  story_join           semantically close to a recent story for the same entity
  new                  a new story
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from newsintel.core.config import policy
from newsintel.core.fingerprints import jaccard, semantic_shingles
from newsintel.core.schemas import Event, EventType, Status
from newsintel.store import models, repo

PERIOD_TYPES = {EventType.earnings, EventType.macro_release}


@dataclass
class DedupeResult:
    outcome: str
    prior_event_id: str | None = None
    story_id: str | None = None
    changed_fields: list[str] = field(default_factory=list)
    shingles: list[str] = field(default_factory=list)


def _flat(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if isinstance(v, dict) and "amount" in v:
            out[k] = (v["amount"], v["currency"])
        elif k not in {"description"}:
            out[k] = v
    return out


def dedupe(s: Session, ev: Event, text: str, form: str | None = None) -> DedupeResult:
    window_h = int(policy()["gate"]["duplicate_window_hours"])
    now = ev.first_detected_at

    if repo.event_by_exact(s, ev.exact_fingerprint):
        return DedupeResult("exact_duplicate")

    shingles = sorted(semantic_shingles(text))[:400]

    # Correction: same entity/type/period as an earlier event, with changed values.
    if ev.primary_entity.id:
        prior = repo.last_same_type_event(s, ev.primary_entity.id, ev.event_type.value, now, exclude_id=ev.id)
        if prior and prior.status in {Status.verified_official.value, Status.corrected.value, Status.corroborated_report.value}:
            pf = prior.payload.get("fields") or {}
            same_period = ev.event_type in PERIOD_TYPES and pf.get("period_end") and pf.get("period_end") == ev.fields.get("period_end")
            amended = bool(form and form.upper().endswith("/A")) and (prior.occurred_at and ev.occurred_at and prior.occurred_at.date() == ev.occurred_at.date())
            if same_period or amended:
                a, b = _flat(pf), _flat(ev.fields)
                changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
                if changed:
                    return DedupeResult("correction", prior.id, prior.story_id, changed, shingles)
                return DedupeResult("structural_duplicate", prior.id, prior.story_id, [], shingles)

    # Structural duplicate inside the window.
    for prior in repo.events_by_structural(s, ev.structural_fingerprint, window_h, now):
        if prior.id != ev.id:
            return DedupeResult("structural_duplicate", prior.id, prior.story_id, [], shingles)

    # Semantic join to a recent story for the same entity.
    mine = set(shingles)
    best, best_score = None, 0.0
    for cand in repo.recent_story_candidates(s, ev.primary_entity.id, now):
        if cand.event_type != ev.event_type.value:
            continue
        story = s.get(models.Story, cand.story_id) if cand.story_id else None
        if not story:
            continue
        sc = jaccard(mine, set(story.shingles or []))
        if sc > best_score:
            best, best_score = story, sc
    if best and best_score >= 0.5:
        return DedupeResult("story_join", None, best.id, [], shingles)

    return DedupeResult("new", None, None, [], shingles)


def window_end(ev: Event) -> timedelta:
    return timedelta(hours=int(policy()["gate"]["duplicate_window_hours"]))
