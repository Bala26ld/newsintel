"""Deterministic identifiers.

Every id is derived from content, not from a counter or a clock, so that replaying the same
fixtures yields the same ids and two runs can be diffed row by row.
"""

from __future__ import annotations

import hashlib


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def derive(prefix: str, *parts: str, length: int = 20) -> str:
    """Stable id: `<prefix>_<hex>` from the ordered parts."""
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{h}"
