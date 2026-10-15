"""Three fingerprints per event.

exact      -> content hash of the normalized document bytes (drops true re-fetches)
structural -> event_type + primary entity + rounded amount + date bucket (links repeats/updates)
semantic   -> a cheap bag-of-words shingle hash in the MVP; pgvector embeddings in Phase 3
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import date

from newsintel.core.ids import content_hash

_WORD = re.compile(r"[a-z0-9]+")


def exact_fingerprint(body: bytes) -> str:
    # Normalize whitespace so a page re-served with different whitespace is still identical.
    text = re.sub(rb"\s+", b" ", body).strip()
    return content_hash(text)


def round_amount(amount: int | None) -> str:
    """Round to two significant figures so '$4.98bn' and '$5.0bn' collide."""
    if not amount:
        return "0"
    digits = int(math.log10(abs(amount))) + 1
    factor = 10 ** max(digits - 2, 0)
    return str(int(round(amount / factor)) * factor)


def structural_fingerprint(event_type: str, entity_id: str | None, amount: int | None, period_key: date | str | None) -> str:
    """type + entity + rounded amount + period key.

    The period key is the reporting period for earnings and macro releases (the same period is
    the same event however many times it is described) and empty for one-off events, where the
    72-hour detection window does the time bucketing instead.
    """
    bucket = period_key.isoformat() if isinstance(period_key, date) else (period_key or "")
    key = "|".join([event_type, entity_id or "unresolved", round_amount(amount), bucket])
    return "sf_" + hashlib.sha1(key.encode()).hexdigest()[:16]


def semantic_shingles(text: str, k: int = 3) -> set[str]:
    words = _WORD.findall(text.lower())
    return {" ".join(words[i : i + k]) for i in range(max(len(words) - k + 1, 0))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
