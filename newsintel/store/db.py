"""Engine, sessions, schema creation and registry seeding."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from newsintel.core.config import ROOT, settings, sources
from newsintel.store import models

# Every row written by the pipeline carries the run id, so a replay run never sees another run's
# events as "prior disclosures" and two runs can be diffed.
current_run_id: ContextVar[str] = ContextVar("run_id", default="live")


@lru_cache
def engine() -> Engine:
    url = settings().database_url
    eng = create_engine(url, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


@contextmanager
def session() -> Iterator[Session]:
    s = _session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db(drop: bool = False) -> None:
    if drop:
        models.Base.metadata.drop_all(engine())
    models.Base.metadata.create_all(engine())
    seed_registry()


def seed_registry() -> None:
    """Load sources.yaml and every universe/entities file into the registry tables (idempotent)."""
    with session() as s:
        for src in sources():
            row = s.get(models.Source, src["id"]) or models.Source(id=src["id"])
            row.market = src["market"]
            row.kind = src["kind"]
            row.access_method = src.get("access_method", "")
            row.poll_interval_s = src.get("poll_interval_s", 60)
            row.terms_url = src.get("terms_url")
            row.enabled = bool(src.get("enabled", False))
            row.may_store_full_text = bool(src.get("may_store_full_text", False))
            row.may_redistribute_text = bool(src.get("may_redistribute_text", False))
            if row.trust_score in (None, 0.5) and "trust_seed" in src:
                row.trust_score = float(src["trust_seed"])
            row.config = {k: v for k, v in src.items() if k not in {"id"}}
            s.merge(row)
            universe = src.get("universe")
            if universe:
                _seed_entities(s, ROOT / universe, src["market"])
        fixture_entities = settings().fixture_dir / "entities.yaml"
        if fixture_entities.exists():
            _seed_entities(s, fixture_entities, None)


def _seed_entities(s: Session, path: Path, default_country: str | None) -> None:
    data = yaml.safe_load(path.read_text()) or {}
    for e in data.get("entities", []):
        row = s.get(models.Entity, e["id"]) or models.Entity(id=e["id"])
        row.canonical_name = e["name"]
        row.country = e.get("country", default_country or "US")
        row.exchange = e.get("exchange")
        row.ticker = e.get("ticker")
        row.cik = e.get("cik")
        row.isin = e.get("isin")
        row.lei = e.get("lei")
        row.bse_code = e.get("bse_code")
        row.aliases = e.get("aliases", [])
        row.fiscal_year_start_month = int(e.get("fiscal_year_start_month", 1))
        s.merge(row)
