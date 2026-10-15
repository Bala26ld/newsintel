"""Claim-level verification.

Each check is deterministic and names itself in `checked_by`. A claim fails closed: any failing
check marks it `unsupported`, and the gate will not publish an event with an unsupported claim.
"""

from __future__ import annotations

from datetime import date, timedelta

from newsintel.core.dates import is_trading_day  # noqa: F401  (kept for calendar checks in Phase 2)
from newsintel.core.ids import derive
from newsintel.core.schemas import CheckStatus, Claim, ClaimDraft, Event, EventType

KNOWN_CURRENCIES = {"USD", "GBP", "EUR", "INR"}


def materialize(ev: Event, drafts: list[ClaimDraft]) -> list[Claim]:
    out = []
    for d in drafts:
        cid = derive("clm", ev.id, d.field, d.span_text, str(d.span.start))
        out.append(Claim(**d.model_dump(), id=cid, event_id=ev.id))
    return out


def verify_claims(ev: Event, claims: list[Claim], doc_text: str | None, source_domain_ok: bool) -> tuple[list[Claim], list[str]]:
    """Run every check on every claim. Returns the updated claims and event-level flags."""
    flags: list[str] = []
    detected = ev.first_detected_at.date()
    for c in claims:
        problems: list[str] = []
        # 1. span reproduces the value's source text
        if doc_text is not None:
            if doc_text[c.span.start : c.span.end] != c.span_text:
                problems.append("span_mismatch")
        # 2. currency and amount sanity
        if c.field.endswith(".amount"):
            if not isinstance(c.value, int) or c.value == 0:
                problems.append("amount_not_integer_or_zero")
            if c.unit not in KNOWN_CURRENCIES:
                problems.append("unknown_currency")
            if isinstance(c.value, int) and abs(c.value) > 10_000_000_000_000:  # > 10 trillion in any unit
                problems.append("amount_implausible")
        if c.field.endswith(".currency") and c.value not in KNOWN_CURRENCIES:
            problems.append("unknown_currency")
        # 3. dates fall in a sane window: within 400 days before detection, up to 400 days after for effective dates
        if c.field in {"agreement_date", "completed_on", "period_end", "effective_on"}:
            try:
                d = date.fromisoformat(str(c.value))
            except ValueError:
                problems.append("date_unparseable")
            else:
                lo, hi = detected - timedelta(days=400), detected + timedelta(days=400)
                if c.field in {"agreement_date", "completed_on", "period_end"}:
                    hi = detected + timedelta(days=1)
                if not (lo <= d <= hi):
                    problems.append("date_out_of_window")
        # 4. percentages
        if c.unit == "%" and not (0 < float(c.value) <= 100):
            problems.append("percentage_out_of_range")
        # 5. source domain
        if not source_domain_ok:
            problems.append("source_domain_unregistered")
        c.check_status = CheckStatus.unsupported if problems else CheckStatus.supported
        c.checked_by = "rule:verify_v1"
        c.reason = ",".join(problems) if problems else None
    # 6. arithmetic consistency for earnings: EPS sign agrees with net income sign
    if ev.event_type == EventType.earnings:
        ni = ev.fields.get("net_income")
        eps = ev.fields.get("diluted_eps")
        if ni and eps is not None and (ni["amount"] < 0) != (eps < 0):
            flags.append("eps_sign_disagrees_with_net_income")
            for c in claims:
                if c.field == "diluted_eps":
                    c.check_status = CheckStatus.unsupported
                    c.reason = "eps_sign_disagrees_with_net_income"
    # 7. registrant / entity identity
    if "registrant_name_mismatch" in ev.ambiguity_flags:
        flags.append("registrant_name_mismatch")
    return claims, flags


def all_supported(claims: list[Claim]) -> bool:
    return all(c.check_status == CheckStatus.supported for c in claims)
