"""Shared helpers for vendor adapters."""

from __future__ import annotations

import time

from newsintel.ai.provider import ModelRequest, ModelResponse, parse_json_block


def cost(pricing: dict | None, input_tokens: int, output_tokens: int) -> float:
    """pricing = {"input_per_m": usd, "output_per_m": usd}; unknown pricing records 0 and is flagged in benchmark."""
    if not pricing:
        return 0.0
    return input_tokens / 1e6 * float(pricing.get("input_per_m", 0)) + output_tokens / 1e6 * float(pricing.get("output_per_m", 0))


def finish(name: str, model: str, text: str, in_tok: int, out_tok: int, started: float, pricing: dict | None, meta: dict | None = None) -> ModelResponse:
    latency = int((time.monotonic() - started) * 1000)
    try:
        output = parse_json_block(text)
        error = None
    except Exception as exc:  # noqa: BLE001
        output, error = None, f"unparseable_json: {exc}"
    return ModelResponse(name, model, output, text, in_tok, out_tok, latency, cost(pricing, in_tok, out_tok), error=error, meta=meta or {})


def failure(name: str, model: str, started: float, err: Exception) -> ModelResponse:
    return ModelResponse(name, model, None, "", 0, 0, int((time.monotonic() - started) * 1000), 0.0, error=f"{type(err).__name__}: {err}")


__all__ = ["ModelRequest", "cost", "finish", "failure"]
