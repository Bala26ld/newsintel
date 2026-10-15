"""Entity resolution against the canonical registry.

Order: exchange identifier (CIK, BSE code, ticker) from the adapter or the document, then
normalized name against canonical names and aliases. A new entity is never created here; an
unresolved name is returned with id=None and raises the `entity_unresolved` flag.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from newsintel.core.schemas import EntityRef
from newsintel.store import models, repo

SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|l\.p|lp|holdings?|group|n\.v|s\.a)\b\.?", re.IGNORECASE)


def normalize_name(name: str) -> str:
    n = name.lower().replace("&", " and ")
    n = SUFFIXES.sub(" ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _score(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def resolve_primary(s: Session, *, cik: str | None, ticker: str | None, name: str | None, threshold: float = 0.92) -> tuple[EntityRef, list[str]]:
    flags: list[str] = []
    row: models.Entity | None = None
    if cik:
        row = repo.entity_by_cik(s, str(cik))
    if row is None and ticker:
        row = repo.entity_by_ticker(s, ticker)
    if row is None and name:
        row = match_by_name(s, name, threshold)
        if row is None:
            flags.append("entity_unresolved")
    if row is None:
        return EntityRef(id=None, name=name or "Unknown registrant"), flags or ["entity_unresolved"]
    if name and normalize_name(name) != normalize_name(row.canonical_name) and normalize_name(name) not in {normalize_name(a) for a in row.aliases}:
        # Identifier matched but the document names someone else: keep the identifier match, flag it.
        if _score(normalize_name(name), normalize_name(row.canonical_name)) < 0.8:
            flags.append("registrant_name_mismatch")
    return EntityRef(id=row.id, name=row.canonical_name, ticker=row.ticker, exchange=row.exchange), flags


def match_by_name(s: Session, name: str, threshold: float = 0.92) -> models.Entity | None:
    target = normalize_name(name)
    best, best_score = None, 0.0
    for row in repo.all_entities(s):
        for cand in [row.canonical_name, *row.aliases]:
            sc = _score(target, normalize_name(cand))
            if sc > best_score:
                best, best_score = row, sc
    return best if best_score >= threshold else None


def resolve_related(s: Session, refs: list[EntityRef]) -> list[EntityRef]:
    out = []
    for r in refs:
        row = match_by_name(s, r.name, threshold=0.95)
        out.append(EntityRef(id=row.id if row else None, name=row.canonical_name if row else r.name, ticker=row.ticker if row else None, role=r.role))
    return out
