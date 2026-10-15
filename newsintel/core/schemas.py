"""Canonical schemas shared by every stage.

Five record types are kept distinct on purpose: raw snapshot, document, event (with claims),
model run, alert. See the design doc, "Data model".
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 1


class EventType(StrEnum):
    contract_award = "contract_award"
    acquisition = "acquisition"
    earnings = "earnings"
    management_change = "management_change"
    regulatory_action = "regulatory_action"
    macro_release = "macro_release"
    other = "other"


class Status(StrEnum):
    developing = "developing"
    verified_official = "verified_official"
    corroborated_report = "corroborated_report"
    corrected = "corrected"
    withdrawn = "withdrawn"
    duplicate = "duplicate"


PUBLISHABLE = {Status.verified_official, Status.corroborated_report, Status.corrected}


class CheckStatus(StrEnum):
    pending = "pending"
    supported = "supported"
    unsupported = "unsupported"


class GateOutcome(StrEnum):
    publish = "publish"
    hold_review = "hold_review"
    hold_developing = "hold_developing"
    merge = "merge"
    suppress = "suppress"


# ---------- value objects ----------


class MoneyField(BaseModel):
    amount: int
    currency: str
    as_stated: str


class Span(BaseModel):
    start: int
    end: int

    @field_validator("end")
    @classmethod
    def _ordered(cls, v: int, info):
        if "start" in info.data and v < info.data["start"]:
            raise ValueError("span end before start")
        return v


class Evidence(BaseModel):
    doc_id: str
    url: str
    hash: str
    label: str
    source_id: str
    published_at: datetime | None = None


class EntityRef(BaseModel):
    id: str | None = None  # None = unresolved
    name: str
    ticker: str | None = None
    exchange: str | None = None
    role: str = "primary"


# ---------- per-event-type field schemas ----------


class ContractAwardFields(BaseModel):
    contract_value: MoneyField | None = None
    customer: str | None = None
    duration_months: int | None = None
    division: str | None = None
    description: str | None = None
    agreement_date: date | None = None


class AcquisitionFields(BaseModel):
    target: str | None = None
    consideration: MoneyField | None = None
    completed_on: date | None = None
    stake_pct: float | None = None
    description: str | None = None


class EarningsFields(BaseModel):
    period_end: date | None = None
    period_label: str | None = None
    revenue: MoneyField | None = None
    net_income: MoneyField | None = None
    diluted_eps: float | None = None
    eps_currency: str | None = None
    guidance_revenue: MoneyField | None = None


class ManagementChangeFields(BaseModel):
    person: str | None = None
    role: str | None = None
    change: Literal["appointed", "resigned", "retired", "terminated"] | None = None
    effective_on: date | None = None
    successor: str | None = None


class RegulatoryActionFields(BaseModel):
    regulator: str | None = None
    action: str | None = None
    penalty: MoneyField | None = None
    effective_on: date | None = None


class MacroReleaseFields(BaseModel):
    indicator: str | None = None
    period: str | None = None
    actual: float | None = None
    prior: float | None = None
    unit: str | None = None


FIELD_SCHEMAS: dict[EventType, type[BaseModel]] = {
    EventType.contract_award: ContractAwardFields,
    EventType.acquisition: AcquisitionFields,
    EventType.earnings: EarningsFields,
    EventType.management_change: ManagementChangeFields,
    EventType.regulatory_action: RegulatoryActionFields,
    EventType.macro_release: MacroReleaseFields,
}

# Human labels for the known-unknowns list, per field.
FIELD_LABELS = {
    "contract_value": "contract value",
    "customer": "customer identity",
    "duration_months": "contract duration",
    "division": "business division",
    "description": "scope of work",
    "agreement_date": "agreement date",
    "target": "target company",
    "consideration": "consideration paid",
    "completed_on": "completion date",
    "stake_pct": "stake acquired",
    "period_end": "reporting period",
    "period_label": "fiscal period label",
    "revenue": "revenue",
    "net_income": "net income",
    "diluted_eps": "diluted EPS",
    "eps_currency": "EPS currency",
    "guidance_revenue": "revenue guidance",
    "person": "person named",
    "role": "role",
    "change": "nature of change",
    "effective_on": "effective date",
    "successor": "successor",
    "regulator": "regulator",
    "action": "action taken",
    "penalty": "penalty amount",
    "indicator": "indicator",
    "period": "period",
    "actual": "actual value",
    "prior": "prior value",
    "unit": "unit",
}

ALWAYS_NOT_ESTABLISHED = ["eventual profitability", "share-price direction"]


# ---------- claims ----------


class ClaimDraft(BaseModel):
    field: str  # dotted path inside `fields`, e.g. "contract_value.amount"
    value: Any
    unit: str | None = None
    evidence_doc_id: str
    span: Span
    span_text: str
    extractor: str  # rule:<name> | model:<provider>/<model> | human:<id>


class Claim(ClaimDraft):
    id: str
    event_id: str
    check_status: CheckStatus = CheckStatus.pending
    checked_by: str | None = None
    reason: str | None = None


# ---------- event ----------


class Event(BaseModel):
    id: str
    event_type: EventType
    market: str
    source_id: str
    primary_entity: EntityRef
    related_entities: list[EntityRef] = Field(default_factory=list)
    occurred_at: datetime | None = None
    source_published_at: datetime | None = None
    first_detected_at: datetime
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    status: Status = Status.developing
    story_id: str | None = None
    exact_fingerprint: str
    structural_fingerprint: str
    materiality: float | None = None
    materiality_parts: dict[str, float] = Field(default_factory=dict)
    ambiguity_flags: list[str] = Field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def typed_fields(self) -> BaseModel | None:
        schema = FIELD_SCHEMAS.get(self.event_type)
        return schema.model_validate(self.fields) if schema else None

    def empty_fields(self) -> list[str]:
        schema = FIELD_SCHEMAS.get(self.event_type)
        if not schema:
            return []
        return [name for name in schema.model_fields if self.fields.get(name) in (None, "", [])]


# ---------- decisions, alerts ----------


class Decision(BaseModel):
    event_id: str
    stage: str
    outcome: str
    reason: str
    rule_id: str | None = None
    at: datetime


class Sentence(BaseModel):
    text: str
    claim_ids: list[str]  # every sentence must cite at least one supported claim


class AlertTimestamps(BaseModel):
    source: datetime | None
    detected: datetime
    published: datetime


class Alert(BaseModel):
    alert_id: str
    version: int
    event_id: str
    story_id: str | None
    status: Status
    event_type: EventType
    market: str
    entity: EntityRef
    headline: str
    confirmed_facts: list[Sentence]
    what_is_new: Sentence | None
    why_it_may_matter: list[Sentence]
    not_established: list[str]
    evidence: list[Evidence]
    timestamps: AlertTimestamps
    materiality_band: Literal["high", "medium", "low"]
    materiality: float
    correction_of: str | None = None
    policy_version: int
    label: str | None = None  # e.g. "Reported by X; official confirmation pending"


class ModelRun(BaseModel):
    id: str
    event_id: str | None
    task: str
    provider: str
    model: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    output: Any
    validated: bool
    at: datetime
