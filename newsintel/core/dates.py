"""Date parsing with spans, market calendars and fiscal-period labelling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from dateutil import parser as dtparser

from newsintel.core.config import markets

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(
    rf"(?P<long>(?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}})|(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})|"
    rf"(?P<dmy>\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FoundDate:
    value: date
    as_stated: str
    span: tuple[int, int]


def find_dates(text: str) -> list[FoundDate]:
    out = []
    for m in DATE_RE.finditer(text):
        try:
            d = dtparser.parse(m.group(0), dayfirst=bool(m.group("dmy"))).date()
        except (ValueError, OverflowError):
            continue
        out.append(FoundDate(d, m.group(0), (m.start(), m.end())))
    return out


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = dtparser.parse(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def market_tz(market: str) -> ZoneInfo:
    return ZoneInfo(markets()[market]["timezone"])


def to_market_local(dt: datetime, market: str) -> datetime:
    return dt.astimezone(market_tz(market))


def is_trading_day(d: date, market: str) -> bool:
    return d.weekday() in set(markets()[market]["trading_days"])


def fiscal_label(period_end: date, fy_start_month: int) -> str:
    """'fiscal Q2 FY26 (Jan–Mar 2026)'-style label from a period end date and FY start month."""
    # Fiscal year is named for the calendar year in which it ends.
    if fy_start_month == 1:
        fy = period_end.year
        q = (period_end.month - 1) // 3 + 1
    else:
        months_into = (period_end.month - fy_start_month) % 12 + 1
        q = (months_into - 1) // 3 + 1
        fy = period_end.year + (1 if period_end.month >= fy_start_month else 0)
    return f"fiscal Q{q} FY{str(fy)[-2:]} (period ended {period_end.isoformat()})"
