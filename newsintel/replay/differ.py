"""Diff two replay runs field by field.

Rows are keyed by run-independent identity (the event's exact fingerprint, a claim's field, an
alert's version) so that two runs of the same fixtures line up even though their ids differ.
Any difference in a published claim or alert sentence fails the build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select

from newsintel.core.ids import derive
from newsintel.store import models
from newsintel.store.db import session


@dataclass
class RunDiff:
    run_a: str
    run_b: str
    events: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)

    @property
    def published_claims_differ(self) -> bool:
        return bool(self.alerts) or any(c.get("published") for c in self.claims)

    @property
    def clean(self) -> bool:
        return not (self.events or self.claims or self.alerts or self.decisions)

    def as_dict(self) -> dict:
        return {"run_a": self.run_a, "run_b": self.run_b, "clean": self.clean, "events": self.events, "claims": self.claims, "alerts": self.alerts, "decisions": self.decisions}


def _snapshot(run_id: str) -> dict:
    with session() as s:
        events = list(s.scalars(select(models.Event).where(models.Event.run_id == run_id)))
        by_id = {e.id: e for e in events}
        fp = {e.id: e.exact_fingerprint for e in events}
        # decisions recorded before an event row exists (e.g. "no_event") use the would-be event id
        for snap in s.scalars(select(models.Snapshot).where(models.Snapshot.run_id == run_id)):
            fp.setdefault(derive("evt", run_id, snap.source_id, snap.content_hash), snap.content_hash)
        ev_map = {
            e.exact_fingerprint: {
                "event_type": e.event_type,
                "entity": e.primary_entity_id,
                "status": e.status,
                "materiality": e.materiality,
                "fields": e.payload.get("fields"),
            }
            for e in events
        }
        published_events = {a.event_id for a in s.scalars(select(models.Alert).where(models.Alert.run_id == run_id))}
        claims = {}
        for c in s.scalars(select(models.Claim).where(models.Claim.event_id.in_(list(by_id)))) if by_id else []:
            claims[(fp[c.event_id], c.field)] = {"value": c.value.get("v"), "span_text": c.span_text, "status": c.check_status, "published": c.event_id in published_events}
        alerts = {}
        for a in s.scalars(select(models.Alert).where(models.Alert.run_id == run_id)):
            body = a.body
            alerts[(fp.get(a.event_id), a.version)] = {
                "headline": a.headline,
                "status": a.status,
                "band": body.get("materiality_band"),
                "facts": [x["text"] for x in body.get("confirmed_facts", [])],
                "new": (body.get("what_is_new") or {}).get("text"),
                "why": [x["text"] for x in body.get("why_it_may_matter", [])],
                "not_established": body.get("not_established"),
                "correction": bool(a.correction_of_alert_id),
            }
        decisions: dict = {}
        for d in s.scalars(select(models.Decision).where(models.Decision.run_id == run_id).order_by(models.Decision.id)):
            key = (fp.get(d.event_id, d.event_id), d.stage)
            decisions.setdefault(key, []).append(d.outcome)
    return {"events": ev_map, "claims": claims, "alerts": alerts, "decisions": decisions}


def diff_runs(run_a: str, run_b: str) -> RunDiff:
    a, b = _snapshot(run_a), _snapshot(run_b)
    out = RunDiff(run_a, run_b)
    for section in ("events", "claims", "alerts", "decisions"):
        sa, sb = a[section], b[section]
        for key in sorted(set(sa) | set(sb), key=str):
            va, vb = sa.get(key), sb.get(key)
            if va != vb:
                entry = {"key": key if isinstance(key, str) else list(key), "a": va, "b": vb}
                if section == "claims" and ((va or {}).get("published") or (vb or {}).get("published")):
                    entry["published"] = True
                getattr(out, section).append(entry)
    return out


def render(d: RunDiff) -> str:
    return json.dumps(d.as_dict(), indent=1, default=str)
