"""Anthropic Messages API adapter."""

from __future__ import annotations

import time

from newsintel.ai.provider import ModelRequest, ModelResponse
from newsintel.ai.providers._base import failure, finish


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str, pricing: dict | None = None):
        self.model, self._key, self.pricing = model, api_key, pricing

    def complete(self, req: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            import anthropic  # lazy: optional dependency

            client = anthropic.Anthropic(api_key=self._key, timeout=req.timeout_s)
            msg = client.messages.create(
                model=self.model,
                max_tokens=req.max_tokens,
                system=req.system,
                messages=[{"role": "user", "content": req.user_message()}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            return finish(self.name, self.model, text, msg.usage.input_tokens, msg.usage.output_tokens, started, self.pricing)
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, self.model, started, exc)
