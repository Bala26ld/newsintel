"""Template summariser: renders alert sentences from supported claims only.

Every sentence carries the ids of the claims it rests on. The model summariser that arrives in
Phase 3 must satisfy the same contract (sentences citing claim ids); the validator in alerts.py
treats both the same way.
"""

from __future__ import annotations

from datetime import date

from newsintel.core.money import format_money
from newsintel.core.schemas import Claim, Event, EventType, Sentence
from newsintel.pipeline.context.compare import ContextResult


def _ids(claims: list[Claim], *fields: str) -> list[str]:
    return [c.id for c in claims if c.field in fields]


def _money(v: dict | None) -> str:
    if not v:
        return ""
    return format_money(abs(int(v["amount"])), v["currency"])


def _d(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"  # no %-d: not supported by the Windows C runtime


def headline(ev: Event, ctx: ContextResult) -> str:
    f, name = ev.fields, ev.primary_entity.name
    t = ev.event_type
    if t == EventType.contract_award:
        value = _money(f.get("contract_value"))
        cust = f.get("customer")
        if value and cust:
            return f"{name} announces {value} contract with {cust}"
        if value:
            return f"{name} announces {value} contract award"
        if cust:
            return f"{name} enters agreement with {cust}; value not disclosed"
        return f"{name} enters material agreement; value not disclosed"
    if t == EventType.acquisition:
        cons = _money(f.get("consideration"))
        stake = f.get("stake_pct")
        what = f"{stake:g}% of {f.get('target')}" if stake else f.get("target", "a business")
        return f"{name} completes acquisition of {what}" + (f" for {cons}" if cons else "")
    if t == EventType.earnings:
        bits = []
        if f.get("revenue"):
            bits.append(f"revenue {_money(f['revenue'])}")
        if f.get("net_income"):
            label = "net loss" if f["net_income"]["amount"] < 0 else "net income"
            bits.append(f"{label} {_money(f['net_income'])}")
        period = f.get("period_label") or "the period"
        head = f"{name} reports {period}" + (": " + ", ".join(bits) if bits else "")
        return ("Correction: " + head) if ctx.diff and ev.status.value == "corrected" else head
    if t == EventType.management_change:
        person, role, change = f.get("person"), f.get("role"), f.get("change")
        if change == "appointed":
            return f"{name} appoints {person} as {role}"
        verb = {"resigned": "resigns", "retired": "retires", "terminated": "is terminated"}.get(change or "", "steps down")
        return f"{name}: {person} {verb} as {role}"
    return f"{name}: {t.value.replace('_', ' ')}"


def confirmed_facts(ev: Event, claims: list[Claim]) -> list[Sentence]:
    f = ev.fields
    out: list[Sentence] = []

    def add(text: str, *fields: str):
        ids = _ids(claims, *fields)
        if ids:
            out.append(Sentence(text=text.replace("..", "."), claim_ids=ids))

    t = ev.event_type
    if t == EventType.contract_award:
        if f.get("contract_value"):
            add(f"The company stated the contract value as {f['contract_value']['as_stated']}.", "contract_value.amount", "contract_value.currency")
        if f.get("customer"):
            add(f"The counterparty is {f['customer']}.", "customer")
        if f.get("duration_months"):
            yrs = f["duration_months"] / 12
            add(f"The stated term is {f['duration_months']} months" + (f" ({yrs:g} years)." if yrs >= 1 else "."), "duration_months")
        if f.get("division"):
            add(f"The work sits in the {f['division']} business.", "division")
        if f.get("agreement_date"):
            add(f"The agreement is dated {_d(f['agreement_date'])}.", "agreement_date")
    elif t == EventType.acquisition:
        add(f"The company completed the acquisition of {f.get('target')}.", "target")
        if f.get("consideration"):
            add(f"Consideration was stated as {f['consideration']['as_stated']}.", "consideration.amount", "consideration.currency")
        if f.get("stake_pct"):
            add(f"The stake acquired is {f['stake_pct']:g}%.", "stake_pct")
        if f.get("completed_on"):
            add(f"Completion date: {_d(f['completed_on'])}.", "completed_on")
    elif t == EventType.earnings:
        if f.get("period_label"):
            add(f"Results cover the {f['period_label']}.", "period_label", "period_end")
        if f.get("revenue"):
            add(f"Revenue was stated as {f['revenue']['as_stated']}.", "revenue.amount", "revenue.currency")
        if f.get("net_income"):
            label = "Net loss" if f["net_income"]["amount"] < 0 else "Net income"
            add(f"{label} was stated as {f['net_income']['as_stated']}.", "net_income.amount", "net_income.currency")
        if f.get("diluted_eps") is not None:
            v = f["diluted_eps"]
            add(f"Diluted {'loss' if v < 0 else 'earnings'} per share: ${abs(v):.2f}.", "diluted_eps")
        if f.get("guidance_revenue"):
            add(f"The company guided to revenue of about {f['guidance_revenue']['as_stated']}.", "guidance_revenue.amount", "guidance_revenue.currency")
    elif t == EventType.management_change:
        change = f.get("change")
        if change == "appointed":
            add(f"{f.get('person')} was appointed {f.get('role')}.", "person", "role", "change")
        else:
            add(f"{f.get('person')} {change} as {f.get('role')}.", "person", "role", "change")
        if f.get("successor"):
            add(f"{f['successor']} was appointed to the role.", "successor")
        if f.get("effective_on"):
            add(f"Effective date: {_d(f['effective_on'])}.", "effective_on")
    return out


def what_is_new(ev: Event, claims: list[Claim], ctx: ContextResult, dedupe_outcome: str, prior_claims: list[Claim]) -> Sentence | None:
    f = ev.fields
    if dedupe_outcome == "correction" and ctx.diff:
        parts = []
        ids: list[str] = []
        for k, d in ctx.diff.items():
            if k in {"description"}:
                continue
            before = d["prior"]["as_stated"] if isinstance(d["prior"], dict) else d["prior"]
            after = d["now"]["as_stated"] if isinstance(d["now"], dict) else d["now"]
            parts.append(f"{k.replace('_', ' ')} previously stated as {before}, now {after}")
            ids += _ids(claims, k, f"{k}.amount", f"{k}.currency") + _ids(prior_claims, k, f"{k}.amount")
        if parts and ids:
            return Sentence(text="Corrects the earlier alert: " + "; ".join(parts) + ".", claim_ids=ids)
    if ctx.prior_event_id is None:
        ids = [c.id for c in claims[:1]]
        return Sentence(text=f"First {ev.event_type.value.replace('_', ' ')} recorded for {ev.primary_entity.name} since monitoring began.", claim_ids=ids) if ids else None
    if ev.event_type == EventType.earnings and ctx.reference and f.get("revenue"):
        now_amt, prior_amt = f["revenue"]["amount"], ctx.reference.amount
        pct = (now_amt - prior_amt) / prior_amt * 100 if prior_amt else 0
        direction = "up" if pct > 0 else "down" if pct < 0 else "unchanged"
        text = f"Revenue is {direction} {abs(pct):.1f}% from {ctx.reference.as_stated} reported for the {ctx.reference.period_label}."
        return Sentence(text=text, claim_ids=_ids(claims, "revenue.amount") + [ctx.reference.claim.id])
    if ctx.diff:
        changed = ", ".join(k.replace("_", " ") for k in ctx.diff if k != "description")
        ids = [c.id for c in claims if c.field.split(".")[0] in ctx.diff]
        if ids:
            return Sentence(text=f"Differs from the last {ev.event_type.value.replace('_', ' ')} on: {changed}.", claim_ids=ids)
    ids = [c.id for c in claims[:1]]
    return Sentence(text="No material difference from the previous disclosure of this type.", claim_ids=ids) if ids else None


def why_it_may_matter(ev: Event, claims: list[Claim], ctx: ContextResult) -> list[Sentence]:
    f, out = ev.fields, []
    t = ev.event_type
    if t in {EventType.contract_award, EventType.acquisition}:
        key = "contract_value" if t == EventType.contract_award else "consideration"
        if f.get(key) and ctx.amount_to_revenue is not None and ctx.reference:
            text = (
                f"The stated amount is about {ctx.amount_to_revenue:.0%} of annualised revenue based on "
                f"{ctx.reference.as_stated} for the {ctx.reference.period_label}; significance depends on margins, duration and execution."
            )
            out.append(Sentence(text=text, claim_ids=_ids(claims, f"{key}.amount") + [ctx.reference.claim.id]))
        elif f.get(key):
            out.append(
                Sentence(text="The filing gives no revenue context for the amount; significance depends on company scale, margins, duration and execution.", claim_ids=_ids(claims, f"{key}.amount"))
            )
        else:
            ids = [c.id for c in claims[:1]]
            if ids:
                out.append(Sentence(text="No value is stated, so the financial significance cannot be assessed from this filing.", claim_ids=ids))
    elif t == EventType.earnings:
        if f.get("guidance_revenue"):
            out.append(Sentence(text=f"Guidance of about {f['guidance_revenue']['as_stated']} sets the reference point for the next report.", claim_ids=_ids(claims, "guidance_revenue.amount")))
        if f.get("net_income") and f["net_income"]["amount"] < 0:
            out.append(Sentence(text="The period was loss-making; the filing does not state the drivers.", claim_ids=_ids(claims, "net_income.amount")))
        if not out and f.get("revenue"):
            out.append(
                Sentence(
                    text="Comparison with consensus is unavailable until licensed estimates are added; prior-period comparison is in the what-is-new line.", claim_ids=_ids(claims, "revenue.amount")
                )
            )
    elif t == EventType.management_change:
        ids = _ids(claims, "role", "change")
        if ids:
            out.append(Sentence(text=f"A change in the {f.get('role')} role; the filing states no reason.", claim_ids=ids))
    return out
