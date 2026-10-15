"""A clock the replay runner can freeze.

Pipeline code never calls datetime.now() directly; it asks the clock. In live mode the clock
is wall time. In replay mode the runner sets it to each snapshot's retrieved_at so that a replayed
run produces timestamps identical to the original, which is what makes two runs diffable.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime

_frozen: ContextVar[datetime | None] = ContextVar("frozen_now", default=None)


def now() -> datetime:
    frozen = _frozen.get()
    return frozen if frozen is not None else datetime.now(UTC)


class freeze:
    """Context manager: `with freeze(ts): ...` makes now() return ts."""

    def __init__(self, at: datetime):
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        self.at = at
        self._token = None

    def __enter__(self):
        self._token = _frozen.set(self.at)
        return self.at

    def __exit__(self, *exc):
        _frozen.reset(self._token)
        return False


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None
