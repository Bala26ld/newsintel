"""Object storage for raw snapshots: a local directory in the MVP, S3-compatible via the same key scheme."""

from __future__ import annotations

from pathlib import Path

from newsintel.core.config import settings


class LocalObjectStore:
    def __init__(self, root: Path | None = None):
        self.root = root or settings().snapshot_dir

    def key_for(self, source_id: str, content_hash: str) -> str:
        h = content_hash.split(":")[1]
        return f"{source_id}/{h[:2]}/{h}"

    def put(self, key: str, body: bytes) -> str:
        p = self.root / key
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()
