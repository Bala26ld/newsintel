"""Model router: slots, budgets, timeouts, alternate-provider retry, deterministic fallback, audit.

Tiers (design doc):
  0 rules                 -> handled in pipeline/extract/tier0.py, never reaches here
  1 fast slot             -> extract (irregular docs), summarise, classify
  2 strong slot           -> contradict (only when materiality >= routing.tier2_min_materiality)
  3 human review          -> gate decision, never a model

Every call writes a model_runs row (prompt hash, tokens, latency, cost, validated flag), so the
benchmark and the audit trail come from the same table.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from newsintel.ai import providers as provider_factory
from newsintel.ai.provider import ModelRequest, ModelResponse, Provider
from newsintel.core import clock
from newsintel.core.config import providers as providers_cfg
from newsintel.core.ids import derive
from newsintel.store import models
from newsintel.store.db import session


@dataclass
class RouteResult:
    response: ModelResponse | None
    validated: bool
    fallback: str | None  # name of the fallback path taken, if any
    run_id: str | None


class Router:
    def __init__(self, cfg: dict | None = None, provider_overrides: dict[str, Provider] | None = None):
        self.cfg = cfg or providers_cfg()
        self._providers: dict[str, Provider] = {}
        self._spend: dict[str, list[tuple[float, float]]] = defaultdict(list)  # provider -> [(t, usd)]
        self._lock = threading.Lock()
        for slot, sc in self.cfg.get("slots", {}).items():
            if provider_overrides and slot in provider_overrides:
                self._providers[slot] = provider_overrides[slot]
                continue
            key_env = sc.get("api_key_env")
            if key_env and not os.environ.get(key_env):
                self._providers[slot] = provider_factory.build("null", sc.get("model", "none"), None)
                continue
            self._providers[slot] = provider_factory.build(sc["provider"], sc["model"], key_env, sc.get("base_url"), sc.get("pricing"))

    # ---- budgets ----

    def _hourly_spend(self, provider: str) -> float:
        cutoff = time.time() - 3600
        with self._lock:
            self._spend[provider] = [(t, c) for t, c in self._spend[provider] if t >= cutoff]
            return sum(c for _, c in self._spend[provider])

    def _over_budget(self, provider: str) -> bool:
        return self._hourly_spend(provider) >= float(self.cfg.get("budgets", {}).get("per_provider_usd_per_hour", 1e9))

    # ---- calls ----

    def call(self, task: str, req: ModelRequest, event_id: str | None = None, validator=None) -> RouteResult:
        tcfg = self.cfg.get("tasks", {}).get(task, {"slot": "fast", "fallback": "skip"})
        slots = [tcfg["slot"]] + [s for s in self._providers if s != tcfg["slot"]]  # primary, then alternates once
        for slot in slots[:2]:
            prov = self._providers.get(slot)
            if prov is None or prov.name == "null" or self._over_budget(prov.name):
                continue
            sc = self.cfg["slots"][slot]
            req.timeout_s = float(sc.get("timeout_s", req.timeout_s))
            req.max_tokens = int(sc.get("max_tokens", req.max_tokens))
            resp = prov.complete(req)
            ok = resp.error is None and resp.output is not None and (validator is None or validator(resp.output))
            run_id = self._record(task, req, resp, ok, event_id)
            with self._lock:
                self._spend[prov.name].append((time.time(), resp.cost_usd))
            if ok:
                return RouteResult(resp, True, None, run_id)
        return RouteResult(None, False, tcfg.get("fallback", "skip"), None)

    def _record(self, task: str, req: ModelRequest, resp: ModelResponse, validated: bool, event_id: str | None) -> str:
        rid = derive("mrun", task, req.prompt_hash(), resp.provider, resp.model, str(time.time_ns()))
        with session() as s:
            s.add(
                models.ModelRun(
                    id=rid,
                    event_id=event_id,
                    task=task,
                    provider=resp.provider,
                    model=resp.model,
                    prompt_hash=req.prompt_hash(),
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    latency_ms=resp.latency_ms,
                    cost_usd=resp.cost_usd,
                    output={"output": resp.output, "error": resp.error},
                    validated=validated,
                    at=clock.now(),
                )
            )
        return rid

    def health(self) -> dict[str, Any]:
        return {slot: {"provider": p.name, "model": p.model, "spend_last_hour_usd": round(self._hourly_spend(p.name), 4), "configured": p.name != "null"} for slot, p in self._providers.items()}


# ---------- task helpers with output contracts ----------

EXTRACT_SYSTEM = (
    "You extract structured fields from a corporate filing. For every field you fill, return the exact verbatim "
    'source span: {"value": ..., "span_text": "<exact text copied from the evidence>"}. Never infer a value that is not '
    "stated. Leave a field null if the document does not state it. Do not add fields. Output JSON only."
)

CONTRADICT_SYSTEM = (
    'You check claims against evidence. Return {"contradictions": [{"claim_id": str, "reason": str, "span_text": str}]}. '
    "Report only places where the evidence contradicts a claim. Never add facts, never restate claims. Output JSON only."
)


def span_validator(doc_text: str):
    """Extraction output is valid only if every span_text occurs verbatim in the document."""

    def _ok(output: Any) -> bool:
        if not isinstance(output, dict):
            return False
        for v in output.values():
            if isinstance(v, dict) and "span_text" in v:
                if not v["span_text"] or v["span_text"] not in doc_text:
                    return False
        return True

    return _ok


def contradiction_validator(claim_ids: set[str], doc_text: str):
    def _ok(output: Any) -> bool:
        if not isinstance(output, dict) or not isinstance(output.get("contradictions"), list):
            return False
        for c in output["contradictions"]:
            if c.get("claim_id") not in claim_ids or not c.get("span_text") or c["span_text"] not in doc_text:
                return False
        return True

    return _ok


def schema_for(event_type: str) -> dict:
    from newsintel.core.schemas import FIELD_SCHEMAS, EventType

    model = FIELD_SCHEMAS[EventType(event_type)]
    base = model.model_json_schema()
    # Each field becomes {"value": <original type>, "span_text": str}
    props = {k: {"type": ["object", "null"], "properties": {"value": v, "span_text": {"type": "string"}}, "required": ["value", "span_text"]} for k, v in base.get("properties", {}).items()}
    return {"type": "object", "properties": props, "additionalProperties": False}


def to_json(obj: Any) -> str:
    return json.dumps(obj, default=str)
