"""Money parsing and formatting across the three markets.

Amounts are stored in whole base currency units (INR, GBP, USD...), never in "crore" or
"million". The original wording is kept beside the number (`as_stated`) so a reader can see
exactly what the source said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

CURRENCY_SYMBOLS = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "£": "GBP",
    "GBP": "GBP",
    "€": "EUR",
    "EUR": "EUR",
    "₹": "INR",
    "Rs": "INR",
    "Rs.": "INR",
    "INR": "INR",
}

SCALES = {
    "thousand": Decimal(1_000),
    "k": Decimal(1_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "million": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "m": Decimal(1_000_000),
    "mm": Decimal(1_000_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "billion": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "b": Decimal(1_000_000_000),
    "trillion": Decimal(1_000_000_000_000),
    "tn": Decimal(1_000_000_000_000),
}

_CUR = r"(?P<cur>US\$|USD|GBP|EUR|INR|Rs\.?|\$|£|€|₹)"
_NUM = r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_SCALE = r"(?:\s*(?P<scale>thousand|lakhs?|lac|million|mn|mm|crores?|cr|billion|bn|trillion|tn|[kmb])\b)?"

_CUR2 = r"(?P<cur2>US\$|USD|GBP|EUR|INR|crores?|cr\b|lakhs?)"
MONEY_RE_CUR_FIRST = re.compile(rf"{_CUR}\s*{_NUM}{_SCALE}", re.IGNORECASE)
MONEY_RE_NUM_FIRST = re.compile(rf"{_NUM}{_SCALE}\s*{_CUR2}", re.IGNORECASE)


class _Match:
    """Adapter so both regex orders expose the same groups."""

    def __init__(self, m: re.Match, cur: str | None, num: str, scale: str | None):
        self.m, self.cur, self.num, self.scale = m, cur, num, scale

    def start(self) -> int:
        return self.m.start()

    def end(self) -> int:
        return self.m.end()


def _search(text: str, start: int) -> _Match | None:
    a = MONEY_RE_CUR_FIRST.search(text, start)
    b = MONEY_RE_NUM_FIRST.search(text, start)
    best = None
    for m in (a, b):
        if m and (best is None or m.start() < best.start()):
            best = m
    if best is None:
        return None
    if best is a:
        return _Match(a, a.group("cur"), a.group("num"), a.group("scale"))
    cur2 = b.group("cur2").lower()
    if cur2.startswith("crore") or cur2 == "cr":
        return _Match(b, "INR", b.group("num"), "crore")
    if cur2.startswith("lakh"):
        return _Match(b, "INR", b.group("num"), "lakh")
    return _Match(b, b.group("cur2"), b.group("num"), b.group("scale"))


@dataclass(frozen=True)
class Money:
    amount: int  # whole currency units
    currency: str
    as_stated: str
    span: tuple[int, int]


def parse_money(text: str, start: int = 0) -> Money | None:
    """Parse the first money expression at or after `start`. Returns None if none found."""
    m = _search(text, start)
    if not m:
        return None
    cur, num, scale = m.cur, m.num, (m.scale or "").lower()
    currency = CURRENCY_SYMBOLS.get(cur) or CURRENCY_SYMBOLS.get(cur.upper()) if cur else None
    if currency is None:
        return None
    value = Decimal(num.replace(",", ""))
    if scale:
        value *= SCALES[scale]
    return Money(amount=int(value), currency=currency, as_stated=text[m.start() : m.end()], span=(m.start(), m.end()))


def find_all_money(text: str) -> list[Money]:
    out, pos = [], 0
    while True:
        m = parse_money(text, pos)
        if not m:
            return out
        out.append(m)
        pos = m.span[1]


def format_money(amount: int, currency: str, style: str = "western") -> str:
    """Human formatting in the market's own convention."""
    if currency == "INR" or style == "indian":
        crore = Decimal(amount) / Decimal(10_000_000)
        if crore >= 1:
            return f"₹{_trim(crore)} crore"
        lakh = Decimal(amount) / Decimal(100_000)
        return f"₹{_trim(lakh)} lakh"
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency, currency + " ")
    for unit, word in ((1_000_000_000_000, "trillion"), (1_000_000_000, "billion"), (1_000_000, "million")):
        if amount >= unit:
            v = Decimal(amount) / Decimal(unit)
            return f"{sym}{_trim(v)} {word}"
    return f"{sym}{amount:,}"


def _trim(v: Decimal) -> str:
    text = f"{v.quantize(Decimal('0.01')):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
