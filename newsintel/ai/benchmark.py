"""Benchmark extraction candidates on the golden set.

Candidates: the Tier 0 rules (always), plus each configured model slot that has a key. Scores
field-level precision and recall against the golden labels, span fidelity, hallucinated fields,
latency and cost. The report is written beside the golden set with the prompt hash, so a prompt
change triggers a rerun.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml

from newsintel.adapters.base import load_fixtures
from newsintel.ai.provider import Evidence, ModelRequest
from newsintel.ai.router import EXTRACT_SYSTEM, Router, schema_for, span_validator
from newsintel.core.config import settings
from newsintel.pipeline.extract.text import parse_8k_meta, parse_document
from newsintel.pipeline.extract.tier0 import extract_8k


def _norm(v: Any) -> Any:
    if isinstance(v, dict) and "amount" in v:
        return (int(v["amount"]), v["currency"])
    if isinstance(v, str):
        return v.strip().lower().rstrip(".")
    return v


DERIVED = {"description", "period_label", "eps_currency"}  # carried for display, not labelled


def score_fields(expected: dict, got: dict) -> dict:
    exp = {k: _norm(v) for k, v in expected.items() if v is not None and k not in DERIVED}
    out = {k: _norm(v) for k, v in got.items() if v is not None and k not in DERIVED}
    tp = sum(1 for k, v in exp.items() if out.get(k) == v)
    fp = sum(1 for k in out if k not in exp or out[k] != exp[k])
    fn = sum(1 for k in exp if out.get(k) != exp[k])
    hallucinated = [k for k in out if k not in exp]
    return {"tp": tp, "fp": fp, "fn": fn, "hallucinated": hallucinated}


def run(golden_path: Path | None = None, fixture_root: Path | None = None) -> dict:
    golden_path = golden_path or settings().fixture_dir / "golden" / "labels.yaml"
    labels = yaml.safe_load(golden_path.read_text())["cases"]
    snaps = {s.meta.get("label"): s for s in load_fixtures(fixture_root or settings().fixture_dir, "sec_edgar")}
    router = Router()
    candidates = {"tier0": None} | {slot: slot for slot, h in router.health().items() if h["configured"]}
    report: dict[str, Any] = {"candidates": {}, "prompt_hash": None, "cases": len(labels)}
    for cand, _slot in candidates.items():
        agg = {"tp": 0, "fp": 0, "fn": 0, "hallucinated": 0, "span_ok": 0, "span_total": 0, "latency_ms": [], "cost_usd": 0.0, "type_correct": 0, "type_total": 0, "failed": 0}
        for label, exp in labels.items():
            snap = snaps.get(label)
            if snap is None:
                continue
            doc = parse_document(snap.body, snap.mime)
            meta = parse_8k_meta(doc.text)
            t0 = time.monotonic()
            if cand == "tier0":
                res = extract_8k(doc.text, "doc", meta["items"])
                got_type = res.event_type.value if res else None
                got_fields = res.fields if res else {}
                spans = [(c.span.start, c.span.end, c.span_text) for c in res.claims] if res else []
                agg["span_total"] += len(spans)
                agg["span_ok"] += sum(1 for a, b, t in spans if doc.text[a:b] == t)
            else:
                if not exp.get("event_type"):
                    continue
                req = ModelRequest(task="extract", system=EXTRACT_SYSTEM, evidence=[Evidence("doc", doc.text)], schema=schema_for(exp["event_type"]))
                rr = router.call("extract", req, validator=span_validator(doc.text))
                report["prompt_hash"] = req.prompt_hash()
                if not rr.validated:
                    agg["failed"] += 1
                    got_type, got_fields = exp["event_type"], {}
                else:
                    got_type = exp["event_type"]
                    got_fields = {k: (v["value"] if isinstance(v, dict) else v) for k, v in rr.response.output.items() if v}
                    agg["cost_usd"] += rr.response.cost_usd
                    agg["span_total"] += len(got_fields)
                    agg["span_ok"] += len(got_fields)  # validator already enforced verbatim spans
            agg["latency_ms"].append(int((time.monotonic() - t0) * 1000))
            agg["type_total"] += 1
            agg["type_correct"] += int(got_type == exp.get("event_type"))
            if exp.get("event_type"):
                sc = score_fields(exp.get("fields", {}), got_fields)
                agg["tp"] += sc["tp"]
                agg["fp"] += sc["fp"]
                agg["fn"] += sc["fn"]
                agg["hallucinated"] += len(sc["hallucinated"])
        lat = sorted(agg["latency_ms"])
        tp, fp, fn = agg["tp"], agg["fp"], agg["fn"]
        report["candidates"][cand] = {
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "type_accuracy": round(agg["type_correct"] / agg["type_total"], 3) if agg["type_total"] else None,
            "span_fidelity": round(agg["span_ok"] / agg["span_total"], 3) if agg["span_total"] else None,
            "hallucinated_fields": agg["hallucinated"],
            "failed_calls": agg["failed"],
            "latency_p50_ms": lat[len(lat) // 2] if lat else None,
            "latency_p95_ms": lat[max(int(len(lat) * 0.95) - 1, 0)] if lat else None,
            "cost_per_1000_docs_usd": round(agg["cost_usd"] / max(len(lat), 1) * 1000, 4),
        }
    out = golden_path.parent / "benchmark_report.json"
    out.write_text(json.dumps(report, indent=1))
    return report
