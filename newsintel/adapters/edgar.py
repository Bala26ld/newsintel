"""SEC EDGAR adapter.

Discovery uses the per-company submissions JSON (data.sec.gov/submissions/CIK##########.json),
which lists recent filings with form type, accession number, filing date, acceptance time and the
primary document name. Fetch downloads the primary document from www.sec.gov/Archives.

Live access is subject to EDGAR's fair-access policy (declared User-Agent, request-rate cap):
confirm the current terms at the `terms_url` in config/sources.yaml before enabling live polling.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from newsintel.adapters.base import AdapterHealth, Candidate, FixtureRecorder, RawSnapshot
from newsintel.adapters.http import PoliteClient
from newsintel.core import clock

EDGAR_TZ = ZoneInfo("America/New_York")


class EdgarAdapter:
    source_id = "sec_edgar"

    def __init__(self, config: dict, entities: list[dict], recorder: FixtureRecorder | None = None, client: PoliteClient | None = None):
        self.config = config
        self.entities = entities  # [{id, name, cik, ...}]
        self.forms = set(config.get("forms", ["8-K"]))
        self.recorder = recorder or FixtureRecorder()
        self.client = client or PoliteClient(
            user_agent=config.get("user_agent", "newsintel/0.1"),
            max_rps=float(config.get("max_requests_per_second", 5)),
            allowed_domains=list(config.get("allowed_domains", [])) + ["data.sec.gov"],
        )
        self._last_success: datetime | None = None
        self._failures = 0
        self._seen: set[str] = set()

    # ---- discovery ----

    def discover(self) -> list[Candidate]:
        out: list[Candidate] = []
        for ent in self.entities:
            cik10 = str(ent["cik"]).zfill(10)
            try:
                resp = self.client.get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
                data = resp.json()
                self._last_success = clock.now()
                self._failures = 0
            except Exception:
                self._failures += 1
                continue
            out.extend(self._candidates_from_submissions(ent, data))
        return out

    def _candidates_from_submissions(self, ent: dict, data: dict) -> list[Candidate]:
        recent = data.get("filings", {}).get("recent", {})
        cols = ["accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument", "items"]
        rows = zip(*(recent.get(c, []) for c in cols), strict=False)
        cik_int = int(str(ent["cik"]).lstrip("0") or "0")
        out = []
        for acc, filing_date, acceptance, form, primary_doc, items in rows:
            if form not in self.forms or not primary_doc:
                continue
            if acc in self._seen:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc.replace('-', '')}/{primary_doc}"
            out.append(
                Candidate(
                    url=url,
                    published_at_hint=parse_acceptance(acceptance),
                    meta={
                        "cik": str(ent["cik"]),
                        "entity_id": ent.get("id"),
                        "registrant": data.get("name") or ent["name"],
                        "accession": acc,
                        "form": form,
                        "filing_date": filing_date,
                        "acceptance": acceptance,
                        "primary_document": primary_doc,
                        "items": [i.strip() for i in (items or "").split(",") if i.strip()],
                        "tickers": data.get("tickers", []),
                    },
                )
            )
        return out

    # ---- fetch ----

    def fetch(self, c: Candidate) -> RawSnapshot:
        resp = self.client.get(c.url)
        self._seen.add(c.meta.get("accession", c.url))
        snap = RawSnapshot(
            source_id=self.source_id,
            url=c.url,
            body=resp.content,
            mime=resp.headers.get("content-type", "text/html").split(";")[0],
            retrieved_at=clock.now(),
            source_published_at=c.published_at_hint,
            headers={k: v for k, v in resp.headers.items() if k.lower() in {"content-type", "last-modified", "etag", "date"}},
            meta=c.meta,
        )
        self.record(snap)
        return snap

    def record(self, s: RawSnapshot) -> None:
        self.recorder.record(s)

    def health(self) -> AdapterHealth:
        return AdapterHealth(self.source_id, self._last_success, self._failures, None)


def parse_acceptance(value: str | None) -> datetime | None:
    """EDGAR acceptanceDateTime is '2026-10-14T16:05:12.000Z' in the submissions API."""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", value)
    if m:
        return datetime(*map(int, m.groups()), tzinfo=UTC)
    # Older index pages use '20261014160512' in Eastern time.
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
    if m:
        return datetime(*map(int, m.groups()), tzinfo=EDGAR_TZ).astimezone(UTC)
    return None


def load_universe(path) -> list[dict]:
    import yaml

    return yaml.safe_load(open(path))["entities"]


__all__ = ["EdgarAdapter", "parse_acceptance", "load_universe", "json"]
