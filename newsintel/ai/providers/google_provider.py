"""Google Gemini adapter via the REST generateContent endpoint (no SDK needed)."""

from __future__ import annotations

import time

import httpx

from newsintel.ai.provider import ModelRequest, ModelResponse
from newsintel.ai.providers._base import failure, finish


class GoogleProvider:
    name = "google"

    def __init__(self, model: str, api_key: str, pricing: dict | None = None):
        self.model, self._key, self.pricing = model, api_key, pricing

    def complete(self, req: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            payload = {
                "system_instruction": {"parts": [{"text": req.system}]},
                "contents": [{"role": "user", "parts": [{"text": req.user_message()}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": req.max_tokens, "responseMimeType": "application/json"},
            }
            with httpx.Client(timeout=req.timeout_s) as client:
                r = client.post(url, json=payload, headers={"x-goog-api-key": self._key})
                r.raise_for_status()
                data = r.json()
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            usage = data.get("usageMetadata", {})
            return finish(self.name, self.model, text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), started, self.pricing)
        except Exception as exc:  # noqa: BLE001
            return failure(self.name, self.model, started, exc)
