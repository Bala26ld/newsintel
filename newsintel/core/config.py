"""Runtime settings and YAML config loading.

Secrets never live here: provider keys are read from the environment variables named in
config/providers.yaml at call time and are never stored or logged.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    """Expand ${VAR:-default} tokens in loaded YAML strings."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEWSINTEL_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{ROOT / 'newsintel.db'}"
    bus: str = "memory"  # memory | redis
    redis_url: str = "redis://localhost:6379/0"
    snapshot_dir: Path = ROOT / "data" / "snapshots"
    fixture_dir: Path = ROOT / "fixtures"
    config_dir: Path = ROOT / "config"
    record_fixtures: bool = True
    contact_email: str = ""
    api_keys_reader: str = "dev-reader-key"
    api_keys_reviewer: str = "dev-reviewer-key"
    log_level: str = "INFO"


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    path = settings().config_dir / name
    with path.open() as fh:
        return _expand_env(yaml.safe_load(fh) or {})


def policy() -> dict[str, Any]:
    return load_yaml("policy.yaml")


def markets() -> dict[str, Any]:
    return load_yaml("markets.yaml")["markets"]


def sources() -> list[dict[str, Any]]:
    return load_yaml("sources.yaml")["sources"]


def providers() -> dict[str, Any]:
    return load_yaml("providers.yaml")
