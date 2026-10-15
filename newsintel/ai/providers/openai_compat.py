"""OpenAI-compatible chat completions adapter (OpenAI, xAI, vLLM, Ollama, LM Studio, ...)."""

from __future__ import annotations

import time

import httpx

from newsintel.ai.provider import ModelRequest, ModelResponse
from newsintel.ai.providers._base import failure, finish


class OpenAICompatibleProvider:
    def __init__(self, name: str, model: str, api_key: str, base_url: str, pricing: dict | None = None):
        self.name, self.model, self._key, self.base_url, self.pricing = name, model, api_key, base_url.rstrip("/"), pricing

    def complete(self, req: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user_message()}],
                "max_tokens": req.max_tokens,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
            with httpx.Client(timeout=req.timeout_s) as client:
                r = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return finish(self.name, self.model, text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), started, self.pricing)
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, self.model, started, exc)
