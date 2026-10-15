"""Tier 0 extractors against the golden labels: every value with a verbatim span."""

from pathlib import Path

import pytest
import yaml

from newsintel.adapters.base import load_fixtures
from newsintel.core.config import ROOT
from newsintel.pipeline.extract.text import parse_8k_meta, parse_document
from newsintel.pipeline.extract.tier0 import extract_8k

GOLDEN = yaml.safe_load((ROOT / "fixtures" / "golden" / "labels.yaml").read_text())["cases"]
SNAPS = {s.meta["label"]: s for s in load_fixtures(Path(ROOT / "fixtures"), "sec_edgar")}


def _norm(v):
    return (v["amount"], v["currency"]) if isinstance(v, dict) and "amount" in v else v


@pytest.mark.parametrize("label", sorted(k for k in GOLDEN if k in SNAPS))
def test_golden_extraction(label):
    exp = GOLDEN[label]
    if exp.get("outcome") == "ingest_exact_duplicate":
        pytest.skip("re-fetch of identical content is dropped at ingest, not by the extractor")
    snap = SNAPS[label]
    doc = parse_document(snap.body, snap.mime)
    meta = parse_8k_meta(doc.text)
    res = extract_8k(doc.text, "doc", meta["items"])
    if exp["event_type"] is None:
        assert res is None
        return
    assert res is not None and res.event_type.value == exp["event_type"]
    for k, v in exp["fields"].items():
        assert _norm(res.fields.get(k)) == _norm(v), f"{label}.{k}"
    for c in res.claims:
        assert doc.text[c.span.start : c.span.end] == c.span_text
