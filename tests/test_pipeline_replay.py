"""Replay the golden fixtures end to end and check outcomes, then replay again and diff."""

import asyncio
import json

import pytest
import yaml
from sqlalchemy import select

from newsintel.core.config import ROOT
from newsintel.replay.differ import diff_runs
from newsintel.replay.runner import replay
from newsintel.store import models
from newsintel.store.db import session

GOLDEN = yaml.safe_load((ROOT / "fixtures" / "golden" / "labels.yaml").read_text())["cases"]


@pytest.fixture(scope="module")
def runs():
    a = asyncio.run(replay("test-a"))
    b = asyncio.run(replay("test-b"))
    return a, b


def _by_label(run_id):
    out = {}
    with session() as s:
        for snap in s.scalars(select(models.Snapshot).where(models.Snapshot.run_id == run_id)):
            label = (snap.http_meta.get("meta") or {}).get("label")
            ev = s.scalars(select(models.Event).where(models.Event.run_id == run_id, models.Event.exact_fingerprint == snap.content_hash)).first()
            gate = [d for d in s.scalars(select(models.Decision).where(models.Decision.run_id == run_id, models.Decision.event_id == (ev.id if ev else ""))) if d.stage in {"gate", "extract"}]
            alerts = list(s.scalars(select(models.Alert).where(models.Alert.run_id == run_id, models.Alert.event_id == (ev.id if ev else "")))) if ev else []
            out[label] = {"event": ev, "decisions": gate, "alerts": alerts}
    return out


def test_outcomes_match_golden(runs):
    a, _ = runs
    got = _by_label("test-a")
    assert a.outcomes.get("ingest:exact_duplicate") == 1
    for label, exp in GOLDEN.items():
        if exp["outcome"] == "ingest_exact_duplicate":
            assert label not in got  # never became a snapshot
            continue
        info = got[label]
        if exp["outcome"] == "no_event":
            assert info["event"] is None
            continue
        ev = info["event"]
        assert ev is not None, label
        assert ev.event_type == exp["event_type"]
        assert ev.primary_entity_id == exp["entity_id"]
        assert ev.status == exp["status"], label
        gate = [d.outcome for d in info["decisions"] if d.stage == "gate"]
        if exp["outcome"] == "published":
            assert gate == ["publish"] and len(info["alerts"]) == 1, label
            if exp.get("correction"):
                assert info["alerts"][0].correction_of_alert_id and info["alerts"][0].version == 2
        elif exp["outcome"] == "merge":
            assert gate == ["merge"] and not info["alerts"]
        elif exp["outcome"] == "hold_review":
            assert gate == ["hold_review"] and not info["alerts"]


def test_every_published_claim_has_a_verbatim_span(runs):
    with session() as s:
        for alert in s.scalars(select(models.Alert).where(models.Alert.run_id == "test-a")):
            claims = list(s.scalars(select(models.Claim).where(models.Claim.event_id == alert.event_id)))
            cited = {cid for sent in alert.body["confirmed_facts"] + alert.body["why_it_may_matter"] + ([alert.body["what_is_new"]] if alert.body["what_is_new"] else []) for cid in sent["claim_ids"]}
            by_id = {c.id: c for c in claims}
            assert cited and cited <= set(by_id), alert.headline
            for cid in cited:
                c = by_id[cid]
                assert c.check_status == "supported"
                doc = s.get(models.Document, c.evidence_doc_id)
                assert doc.text[c.span_start : c.span_end] == c.span_text


def test_no_advisory_language(runs):
    with session() as s:
        for alert in s.scalars(select(models.Alert).where(models.Alert.run_id == "test-a")):
            text = json.dumps(alert.body).lower()
            for bad in (" buy ", " sell ", "price target", "will rise", "will fall"):
                assert bad not in text


def test_replay_is_deterministic(runs):
    d = diff_runs("test-a", "test-b")
    assert d.clean, d.as_dict()
