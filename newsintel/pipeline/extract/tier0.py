"""Tier 0 deterministic extractors for 8-K items 1.01, 2.01, 2.02 and 5.02.

Every value is written as a ClaimDraft with the exact span it came from. Nothing is inferred
from a headline, and nothing is filled in from outside the document. If an extractor cannot
find a value, the field stays empty and appears in the alert's "not established" list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from newsintel.core.dates import find_dates
from newsintel.core.money import Money, parse_money
from newsintel.core.schemas import ClaimDraft, EntityRef, EventType, Span
from newsintel.pipeline.extract.text import item_section

NAME = (
    r"(?:[Tt]he\s+)?[A-Z][A-Za-z0-9&'.\-]*(?:\s+(?:of|and|&|de|the|[A-Z][A-Za-z0-9&'.\-]*)){0,6}(?:,?\s+(?:Inc\.?|Corp\.?|Corporation|LLC|L\.P\.|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.|Co\.|Company))?"
)
ROLE = r"(?:Chief\s+[A-Z][a-z]+\s+Officer|President|Chairman|Chair|Chief Executive Officer|Chief Financial Officer|Chief Operating Officer|Chief Technology Officer|General Counsel|Executive Vice President|Senior Vice President|Vice President|Director|Treasurer|Controller|Principal Accounting Officer)"


@dataclass
class ExtractResult:
    event_type: EventType
    fields: dict = field(default_factory=dict)
    claims: list[ClaimDraft] = field(default_factory=list)
    occurred_on: date | None = None
    related: list[EntityRef] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    extractor: str = "rule:unknown"
    section: tuple[int, int] = (0, 0)  # character range of the item body the event came from


class Ctx:
    def __init__(self, text: str, doc_id: str, rule: str):
        self.text, self.doc_id, self.rule = text, doc_id, rule
        self.claims: list[ClaimDraft] = []

    def claim(self, field_path: str, value, start: int, end: int, unit: str | None = None) -> ClaimDraft:
        c = ClaimDraft(
            field=field_path,
            value=value,
            unit=unit,
            evidence_doc_id=self.doc_id,
            span=Span(start=start, end=end),
            span_text=self.text[start:end],
            extractor=f"rule:{self.rule}",
        )
        self.claims.append(c)
        return c

    def money(self, field_path: str, m: Money, offset: int = 0) -> dict:
        s, e = m.span[0] + offset, m.span[1] + offset
        self.claim(f"{field_path}.amount", m.amount, s, e, unit=m.currency)
        self.claim(f"{field_path}.currency", m.currency, s, e)
        return {"amount": m.amount, "currency": m.currency, "as_stated": m.as_stated}


APPROX = re.compile(r"\s*(?:approximately|about|roughly|around|an\s+estimated|up\s+to|in\s+excess\s+of|at\s+least|more\s+than)?\s*", re.IGNORECASE)


def money_after(body: str, pos: int) -> Money | None:
    """Money expression that starts right after `pos`, allowing an approximation word in between."""
    skip = APPROX.match(body, pos)
    start = skip.end() if skip else pos
    m = parse_money(body, start)
    if m and m.span[0] - start < 2:
        return m
    return None


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    start = max(text.rfind(". ", 0, pos), text.rfind("\n", 0, pos))
    start = start + 2 if start >= 0 and text[start] == "." else (start + 1 if start >= 0 else 0)
    end_candidates = [i for i in (text.find(". ", pos), text.find("\n", pos)) if i >= 0]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    return start, end


def _on_date(text: str, start: int, end: int) -> tuple[date, int, int] | None:
    m = re.search(r"\bOn\s+([A-Z][a-z]+ \d{1,2}, \d{4})", text[start:end])
    if not m:
        return None
    ds = find_dates(m.group(1))
    if not ds:
        return None
    return ds[0].value, start + m.start(1), start + m.end(1)


# ---------------- Item 1.01 / 8.01: contract award ----------------

CONTRACT_WORDS = re.compile(r"\b(contract|purchase order|supply agreement|services agreement|master agreement|order|award(?:ed)?|definitive agreement)\b", re.IGNORECASE)
FINANCING_WORDS = re.compile(r"\b(credit agreement|credit facility|indenture|notes due|term loan|revolving|underwriting agreement|merger agreement)\b", re.IGNORECASE)
VALUE_CUE = re.compile(
    r"(valued at|worth|total value of|aggregate value of|contract value of|value of up to|value of approximately|value of|of approximately|up to|totaling|totalling)\s+", re.IGNORECASE
)
CUSTOMER_RE = re.compile(
    rf"\b(?:with|from|awarded by)\s+({NAME})",
)
DURATION_RE = re.compile(r"\b(\d{1,2})[- ]year\b|\b(\d{1,3})[- ]month\b|over\s+(\d{1,2})\s+years", re.IGNORECASE)
DIVISION_RE = re.compile(r"\b(?:its|the Company's|our)\s+([A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+){0,3})\s+(?:division|segment|business unit|business)\b")


def extract_contract(text: str, doc_id: str, item: str = "1.01") -> ExtractResult | None:
    s, e = item_section(text, item)
    body = text[s:e]
    if not CONTRACT_WORDS.search(body):
        return None
    if FINANCING_WORDS.search(body) and not re.search(r"\b(customer|supply|services|deliver|order)\b", body, re.IGNORECASE):
        return None  # a financing agreement, not a commercial contract: out of MVP scope
    ctx = Ctx(text, doc_id, "contract_award_v1")
    fields: dict = {}
    flags: list[str] = []

    money = None
    for cue in VALUE_CUE.finditer(body):
        money = money_after(body, cue.end())
        if money:
            break
    if money:
        fields["contract_value"] = ctx.money("contract_value", money, offset=s)
        sb, se = _sentence_bounds(text, s + money.span[0])
        fields["description"] = text[sb:se].strip()
        ctx.claim("description", fields["description"], sb, se)
    else:
        flags.append("no_contract_value")

    cm = CUSTOMER_RE.search(body)
    if cm:
        cust = cm.group(1).strip().rstrip(",")
        if cust.lower() not in {"the company", "the registrant"} and len(cust) > 2:
            fields["customer"] = cust
            ctx.claim("customer", cust, s + cm.start(1), s + cm.end(1))
    dm = DURATION_RE.search(body)
    if dm:
        months = int(dm.group(1) or dm.group(3)) * 12 if (dm.group(1) or dm.group(3)) else int(dm.group(2))
        fields["duration_months"] = months
        ctx.claim("duration_months", months, s + dm.start(), s + dm.end(), unit="months")
    dv = DIVISION_RE.search(body)
    if dv:
        fields["division"] = dv.group(1)
        ctx.claim("division", dv.group(1), s + dv.start(1), s + dv.end(1))
    occurred = None
    od = _on_date(text, s, e)
    if od:
        occurred = od[0]
        fields["agreement_date"] = od[0].isoformat()
        ctx.claim("agreement_date", od[0].isoformat(), od[1], od[2])
    related = [EntityRef(name=fields["customer"], role="customer")] if fields.get("customer") else []
    return ExtractResult(EventType.contract_award, fields, ctx.claims, occurred, related, flags, "rule:contract_award_v1", (s, e))


# ---------------- Item 2.02: earnings ----------------

REVENUE_RE = re.compile(r"\b(?:total\s+)?(?:net\s+)?revenues?\s+(?:for\s+the\s+[^.\n]{0,40}?\s+)?(?:of|was|were|totaled|totalled|came in at|reached)\s+", re.IGNORECASE)
REVENUE_ALT_RE = re.compile(r"\b(?:total\s+)?(?:net\s+)?revenues?\b[^.\n]{0,80}?\b(?:increased|decreased|grew|declined|rose|fell)\b[^.\n]{0,40}?\bto\s+", re.IGNORECASE)
NET_INCOME_RE = re.compile(r"\bnet\s+(?:income|earnings|loss)\s+(?:attributable to [^\n.]{0,40}?\s+)?(?:of|was|were|totaled|totalled)\s+", re.IGNORECASE)
EPS_RE = re.compile(r"(?:diluted\s+(?:earnings|net income|EPS)[^.\n]{0,30}?(?:of|was)\s+\$(\d+\.\d{2}))|(?:\$(\d+\.\d{2})\s+per\s+diluted\s+share)", re.IGNORECASE)
PERIOD_RE = re.compile(
    r"\b(?:(first|second|third|fourth)\s+(?:fiscal\s+)?quarter|(three|six|nine|twelve)\s+months|fiscal\s+(?:year|quarter)|quarter|year)\s+(?:ended|ending)\s+([A-Z][a-z]+ \d{1,2}, \d{4})",
    re.IGNORECASE,
)
GUIDANCE_RE = re.compile(r"\b(?:expects?|anticipates?|guidance[^.\n]{0,40}?)\s+[^.\n]{0,80}?revenue[^.\n]{0,60}?(?:of|to be|between|in the range of|approximately)\s+", re.IGNORECASE)


def extract_earnings(text: str, doc_id: str) -> ExtractResult | None:
    s, e = item_section(text, "2.02")
    body = text[s:e]
    if "results" not in body.lower() and "earnings" not in body.lower() and "revenue" not in body.lower():
        return None
    ctx = Ctx(text, doc_id, "earnings_v1")
    fields: dict = {}
    flags: list[str] = []

    pm = PERIOD_RE.search(body)
    occurred = None
    if pm:
        ds = find_dates(pm.group(3))
        if ds:
            fields["period_end"] = ds[0].value.isoformat()
            fields["period_label"] = body[pm.start() : pm.end()].strip()
            ctx.claim("period_end", ds[0].value.isoformat(), s + pm.start(3), s + pm.end(3))
            ctx.claim("period_label", fields["period_label"], s + pm.start(), s + pm.end())
            occurred = ds[0].value

    for name, rx in (("revenue", REVENUE_RE), ("revenue", REVENUE_ALT_RE), ("net_income", NET_INCOME_RE), ("guidance_revenue", GUIDANCE_RE)):
        if name in fields:
            continue
        for m in rx.finditer(body):
            money = money_after(body, m.end())
            if money:
                if name == "net_income" and re.search(r"\bloss\b", body[m.start() : m.end()], re.IGNORECASE):
                    money = Money(-money.amount, money.currency, money.as_stated, money.span)
                fields[name] = ctx.money(name, money, offset=s)
                break

    em = EPS_RE.search(body)
    if em:
        val = float(em.group(1) or em.group(2))
        ni = fields.get("net_income")
        if ni and ni["amount"] < 0:
            sb, se = _sentence_bounds(body, em.start())
            if re.search(r"\bloss\b", body[sb:se], re.IGNORECASE):
                val = -val
        fields["diluted_eps"] = val
        fields["eps_currency"] = "USD"
        ctx.claim("diluted_eps", val, s + em.start(), s + em.end(), unit="USD")
    if "revenue" not in fields:
        flags.append("no_revenue_figure")
    return ExtractResult(EventType.earnings, fields, ctx.claims, occurred, [], flags, "rule:earnings_v1", (s, e))


# ---------------- Item 2.01: acquisition ----------------

ACQ_RE = re.compile(
    rf"\b(?:completed|consummated|closed)\s+(?:the|its)\s+(?:previously\s+announced\s+)?(?:acquisition|purchase)\s+of\s+(?:(?:all\s+(?:of\s+)?|\d{{1,3}}(?:\.\d+)?%\s+of\s+)the\s+(?:issued\s+and\s+)?outstanding\s+(?:shares|equity)\s+of\s+)?({NAME})"
)
STAKE_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)%\s+(?:of\s+the\s+)?(?:outstanding\s+)?(?:shares|equity|interest|stake)", re.IGNORECASE)
CONSIDERATION_RE = re.compile(
    r"\b(?:aggregate\s+)?(?:purchase\s+price|consideration|cash\s+consideration)\s+(?:of|was|totaling|totalling)\s+(?:approximately\s+)?|\bfor\s+(?:an\s+aggregate\s+|a\s+total\s+|approximately\s+)?(?:purchase\s+price\s+of\s+|consideration\s+of\s+|cash\s+consideration\s+of\s+)?(?=(?:US)?\$)",
    re.IGNORECASE,
)


def extract_acquisition(text: str, doc_id: str) -> ExtractResult | None:
    s, e = item_section(text, "2.01")
    body = text[s:e]
    am = ACQ_RE.search(body)
    if not am:
        return None
    ctx = Ctx(text, doc_id, "acquisition_v1")
    fields: dict = {"target": am.group(1).strip().rstrip(",")}
    ctx.claim("target", fields["target"], s + am.start(1), s + am.end(1))
    flags: list[str] = []
    for cm in CONSIDERATION_RE.finditer(body):
        money = money_after(body, cm.end())
        if money:
            fields["consideration"] = ctx.money("consideration", money, offset=s)
            sb, se = _sentence_bounds(text, s + money.span[0])
            fields["description"] = text[sb:se].strip()
            ctx.claim("description", fields["description"], sb, se)
            break
    if "consideration" not in fields:
        flags.append("no_consideration")
    sm = STAKE_RE.search(body)
    if sm:
        fields["stake_pct"] = float(sm.group(1))
        ctx.claim("stake_pct", fields["stake_pct"], s + sm.start(), s + sm.end(), unit="%")
    occurred = None
    od = _on_date(text, s, e)
    if od:
        occurred = od[0]
        fields["completed_on"] = od[0].isoformat()
        ctx.claim("completed_on", od[0].isoformat(), od[1], od[2])
    related = [EntityRef(name=fields["target"], role="target")]
    return ExtractResult(EventType.acquisition, fields, ctx.claims, occurred, related, flags, "rule:acquisition_v1", (s, e))


# ---------------- Item 5.02: management change ----------------

APPOINT_RE = re.compile(rf"\bappointed\s+({NAME})\s+(?:as|to serve as|to the position of)\s+(?:the\s+Company's\s+|its\s+|the\s+)?({ROLE})")
DEPART_RE = re.compile(
    rf"\b({NAME})\s+(?:notified the Company of (?:his|her|their) (?:intention|decision) to\s+)?(resigned|retired|will retire|was terminated|stepped down|will step down)\s+(?:as|from)\s+(?:his|her|their|the)?\s*(?:position\s+as\s+|role\s+as\s+|position\s+of\s+)?({ROLE})"
)
EFFECTIVE_RE = re.compile(r"\beffective\s+(?:as\s+of\s+)?([A-Z][a-z]+ \d{1,2}, \d{4})")
SUCCESSOR_RE = re.compile(rf"\b(?:succeed(?:s|ed|ing)?|replac(?:es|ed|ing))\s+({NAME})")


def extract_management(text: str, doc_id: str) -> ExtractResult | None:
    s, e = item_section(text, "5.02")
    body = text[s:e]
    ctx = Ctx(text, doc_id, "management_change_v1")
    fields: dict = {}
    dm = DEPART_RE.search(body)
    am = APPOINT_RE.search(body)
    if dm and (not am or dm.start() < am.start()):
        fields["person"] = dm.group(1).strip()
        fields["role"] = dm.group(3).strip()
        verb = dm.group(2).lower()
        fields["change"] = "retired" if "retire" in verb else "terminated" if "terminated" in verb else "resigned"
        ctx.claim("person", fields["person"], s + dm.start(1), s + dm.end(1))
        ctx.claim("role", fields["role"], s + dm.start(3), s + dm.end(3))
        ctx.claim("change", fields["change"], s + dm.start(2), s + dm.end(2))
        if am:
            fields["successor"] = am.group(1).strip()
            ctx.claim("successor", fields["successor"], s + am.start(1), s + am.end(1))
    elif am:
        fields["person"] = am.group(1).strip()
        fields["role"] = am.group(2).strip()
        fields["change"] = "appointed"
        ctx.claim("person", fields["person"], s + am.start(1), s + am.end(1))
        ctx.claim("role", fields["role"], s + am.start(2), s + am.end(2))
        ctx.claim("change", "appointed", s + am.start(), s + am.start() + len("appointed"))
    else:
        return None
    occurred = None
    ef = EFFECTIVE_RE.search(body)
    if ef:
        ds = find_dates(ef.group(1))
        if ds:
            occurred = ds[0].value
            fields["effective_on"] = occurred.isoformat()
            ctx.claim("effective_on", fields["effective_on"], s + ef.start(1), s + ef.end(1))
    return ExtractResult(EventType.management_change, fields, ctx.claims, occurred, [], [], "rule:management_change_v1", (s, e))


# ---------------- dispatcher ----------------

ITEM_PRIORITY = ["2.01", "2.02", "1.01", "5.02", "8.01"]


def extract_8k(text: str, doc_id: str, items: list[str]) -> ExtractResult | None:
    """Run the extractor for the highest-priority supported item present; None if nothing matches."""
    for item in ITEM_PRIORITY:
        if item not in items:
            continue
        res = None
        if item == "2.01":
            res = extract_acquisition(text, doc_id)
        elif item == "2.02":
            res = extract_earnings(text, doc_id)
        elif item in ("1.01", "8.01"):
            res = extract_contract(text, doc_id, item)
        elif item == "5.02":
            res = extract_management(text, doc_id)
        if res:
            return res
    return None
