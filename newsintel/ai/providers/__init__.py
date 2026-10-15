"""Vendor adapters. Each is a thin wrapper; SDKs are imported lazily so the pipeline runs without them."""

from __future__ import annotations

import os

from newsintel.ai.provider import Provider


def build(provider: str, model: str, api_key_env: str | None, base_url: str | None = None, pricing: dict | None = None) -> Provider:
    key = os.environ.get(api_key_env, "") if api_key_env else ""
    if provider == "anthropic":
        from newsintel.ai.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=model, api_key=key, pricing=pricing)
    if provider in {"openai", "openai_compatible", "xai"}:
        from newsintel.ai.providers.openai_compat import OpenAICompatibleProvider

        default = {"openai": "https://api.openai.com/v1", "xai": "https://api.x.ai/v1"}.get(provider)
        return OpenAICompatibleProvider(name=provider, model=model, api_key=key, base_url=base_url or default or "", pricing=pricing)
    if provider in {"google", "gemini"}:
        from newsintel.ai.providers.google_provider import GoogleProvider

        return GoogleProvider(model=model, api_key=key, pricing=pricing)
    if provider == "null":
        from newsintel.ai.providers.null_provider import NullProvider

        return NullProvider(model=model)
    raise ValueError(f"unknown provider {provider!r}")
