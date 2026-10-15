"""Adapter contract, raw snapshot type and the fixture recorder.

An adapter does four things and nothing else: discover candidates, fetch one, report health,
and record what it fetched as a replay fixture. It never parses event content.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from newsintel.core import clock
from newsintel.core.config import settings
from newsintel.core.dates import parse_datetime
from newsintel.core.fingerprints import exact_fingerprint


@dataclass
class Candidate:
    url: str
    published_at_hint: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # form type, cik, accession no, title...


@dataclass
class RawSnapshot:
    source_id: str
    url: str
    body: bytes
    mime: str
    retrieved_at: datetime
    source_published_at: datetime | None
    headers: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return exact_fingerprint(self.body)

    def to_fixture(self) -> dict[str, Any]:
        text_ok = self.mime.startswith("text/") or "html" in self.mime or "xml" in self.mime or "json" in self.mime
        d: dict[str, Any] = {
            "source_id": self.source_id,
            "url": self.url,
            "mime": self.mime,
            "retrieved_at": clock.iso(self.retrieved_at),
            "source_published_at": clock.iso(self.source_published_at),
            "headers": self.headers,
            "meta": self.meta,
        }
        if text_ok:
            d["body"] = self.body.decode("utf-8", errors="replace")
        else:
            d["body_b64"] = base64.b64encode(self.body).decode("ascii")
        return d

    @classmethod
    def from_fixture(cls, d: dict[str, Any]) -> RawSnapshot:
        body = d["body"].encode("utf-8") if "body" in d else base64.b64decode(d["body_b64"])
        return cls(
            source_id=d["source_id"],
            url=d["url"],
            body=body,
            mime=d.get("mime", "text/html"),
            retrieved_at=parse_datetime(d["retrieved_at"]),
            source_published_at=parse_datetime(d.get("source_published_at")),
            headers=d.get("headers", {}),
            meta=d.get("meta", {}),
        )


@dataclass
class AdapterHealth:
    source_id: str
    last_success_at: datetime | None
    consecutive_failures: int
    lag_estimate_s: float | None
    note: str = ""


class SourceAdapter(Protocol):
    source_id: str

    def discover(self) -> list[Candidate]: ...

    def fetch(self, c: Candidate) -> RawSnapshot: ...

    def health(self) -> AdapterHealth: ...

    def record(self, s: RawSnapshot) -> None: ...


class FixtureRecorder:
    """Writes every raw snapshot as fixtures/<source>/<yyyy-mm-dd>/<hash>.json. Always on by default."""

    def __init__(self, root: Path | None = None, enabled: bool | None = None) -> None:
        self.root = root or settings().fixture_dir
        self.enabled = settings().record_fixtures if enabled is None else enabled

    def path_for(self, s: RawSnapshot) -> Path:
        day = s.retrieved_at.date().isoformat()
        stamp = s.retrieved_at.strftime("%H%M%S")
        return self.root / s.source_id / day / f"{stamp}-{s.content_hash.split(':')[1][:20]}.json"

    def record(self, s: RawSnapshot) -> Path | None:
        if not self.enabled:
            return None
        p = self.path_for(s)
        if p.exists():
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s.to_fixture(), indent=1, ensure_ascii=False))
        return p


def load_fixtures(root: Path, source_id: str | None = None) -> list[RawSnapshot]:
    """All fixtures under root (optionally one source), ordered by retrieved_at then path."""
    out: list[tuple[datetime, str, RawSnapshot]] = []
    base = root / source_id if source_id else root
    for p in sorted(base.rglob("*.json")):
        if p.parent.name in {"golden"} or "golden" in p.parts:
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if "source_id" not in d or "retrieved_at" not in d:
            continue
        snap = RawSnapshot.from_fixture(d)
        out.append((snap.retrieved_at, str(p), snap))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out]
