"""A provider that never answers: used in tests and when no key is configured, to exercise fallbacks."""

from __future__ import annotations

from newsintel.ai.provider import ModelRequest, ModelResponse


class NullProvider:
    name = "null"

    def __init__(self, model: str = "none", canned: dict | None = None):
        self.model, self.canned = model, canned

    def complete(self, req: ModelRequest) -> ModelResponse:
        if self.canned is not None:
            import json

            text = json.dumps(self.canned)
            return ModelResponse(self.name, self.model, self.canned, text, len(req.user_message()) // 4, len(text) // 4, 1, 0.0)
        return ModelResponse(self.name, self.model, None, "", 0, 0, 0, 0.0, error="no provider configured")
