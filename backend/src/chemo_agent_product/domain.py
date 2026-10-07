"""Versioned internal contracts. No clinical values or hospital codes are invented here."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def fingerprint(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reference(Contract):
    namespace: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Fact(Contract):
    code: str = Field(min_length=1)
    value: str | float | bool | None
    unit: str | None = None
    observed_at: AwareDatetime | None = None
    status: Literal["CONFIRMED", "MISSING", "CONFLICT", "UNCERTAIN", "NEGATED"]
    source: Reference
    locator: dict[str, Any] = Field(default_factory=dict)


class PatientSnapshot(Contract):
    schema_version: Literal["facts.v1"] = "facts.v1"
    patient_ref: str = Field(min_length=1)
    encounter_ref: str = Field(min_length=1)
    captured_at: AwareDatetime
    facts: dict[str, Fact]
    text_records: list[dict[str, Any]] = Field(default_factory=list)


class Finding(Contract):
    code: str
    message: str
    field_path: str | None = None
    patient_value: Any | None = None
    source_refs: list[Reference] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class FactRequirement(Contract):
    fact_code: str
    unit: str | None = None
    max_age_days: int | None = Field(default=None, ge=0)


class EvidenceReference(Contract):
    ref: Reference
    source_code: str
    scope: Literal["REGIMEN", "DRUG_ONLY"]
    status: Literal["PUBLISHED", "DRAFT", "RETIRED"]
    applicable: bool
    excerpt: str
    source_locator: dict[str, Any] = Field(default_factory=dict)
    source_recommendation_raw: str | None = None
    source_evidence_category_raw: str | None = None


class RuleCondition(Contract):
    fact_code: str
    operator: Literal["eq", "lt", "lte", "gt", "gte", "in"]
    value: str | float | bool | list[str]
    unit: str | None = None
    max_age_days: int | None = Field(default=None, ge=0)


class ClinicalRule(Contract):
    ref: Reference
    kind: Literal["ABSOLUTE_CONTRAINDICATION", "SAFETY_NOTICE"]
    status: Literal["PUBLISHED", "DRAFT", "RETIRED"]
    condition: RuleCondition
    message: str
    responsible_drug: str | None = None
    source_excerpt: str


class Applicability(Contract):
    ref: Reference
    status: Literal["PUBLISHED", "DRAFT", "RETIRED"]
    disease_codes: list[str]
    pathology_codes: list[str]
    pathology_unrestricted: bool = False
    relation: Literal["CURRENT_DISEASE", "RELATED_OFF_LABEL"]
    description: str


class CandidateInput(Contract):
    regimen_id: UUID
    version_id: UUID
    regimen_code: str
    display_name: str
    template_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_status: Literal["PUBLISHED", "DRAFT", "RETIRED"]
    applicability: Applicability
    requirements: list[FactRequirement] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    rules: list[ClinicalRule] = Field(default_factory=list)


class CandidateAssessment(Contract):
    regimen_id: UUID
    version_id: UUID
    regimen_code: str
    display_name: str
    presentation_region: Literal["RECOMMENDATION", "X_EXCLUDED", "NOT_DISPLAYED"]
    evidence_state: Literal["VERIFIED", "TEST_REFERENCE", "NO_APPROVED_EVIDENCE"]
    evidence_level: int | None
    evidence_grade: str | None
    selected_evidence_ref: Reference | None
    evidence_refs: list[Reference]
    data_labels: list[Finding]
    safety_labels: list[Finding]
    x_reason_code: Literal["RELATED_OFF_LABEL", "ABSOLUTE_CONTRAINDICATION"] | None
    x_basis: list[dict[str, Any]]
    applicability: Applicability


class DecisionResult(Contract):
    schema_version: Literal["decision.v1"] = "decision.v1"
    policy_version: Literal["V2.2-FDA5-CSCO7-v1"] = "V2.2-FDA5-CSCO7-v1"
    usage_mode: Literal["TEST_ONLY", "CLINICAL"]
    outcome_code: Literal["CANDIDATES", "NO_CANDIDATE", "NEEDS_INPUT", "NEEDS_REVIEW"]
    candidates: list[CandidateAssessment]
    notices: list[Finding] = Field(default_factory=list)


class Calculation(Contract):
    formula_version: str
    state: Literal["COMPUTED", "NOT_COMPUTABLE"]
    value: float | None = None
    unit: str | None = None
    display_value: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[Reference] = Field(default_factory=list)
    reasons: list[Finding] = Field(default_factory=list)


def usable_fact(
    snapshot: PatientSnapshot, requirement: FactRequirement, now: datetime
) -> tuple[Fact | None, Finding | None]:
    fact = snapshot.facts.get(requirement.fact_code)
    path = f"facts.{requirement.fact_code}"
    if fact is None or fact.value is None or fact.status != "CONFIRMED":
        return None, Finding(
            code="DATA_UNCONFIRMED",
            message="数据缺失、冲突或尚未确认",
            field_path=path,
            source_refs=[fact.source] if fact else [],
        )
    if requirement.unit and fact.unit != requirement.unit:
        return None, Finding(
            code="UNIT_UNRESOLVED",
            message="单位尚未正确映射",
            field_path=path,
            patient_value=fact.value,
            source_refs=[fact.source],
        )
    if requirement.max_age_days is not None:
        if fact.observed_at is None:
            return None, Finding(
                code="TIME_MISSING",
                message="缺少采集时间",
                field_path=path,
                source_refs=[fact.source],
            )
        age = (now - fact.observed_at).total_seconds() / 86400
        if age < 0 or age > requirement.max_age_days:
            return None, Finding(
                code="DATA_EXPIRED",
                message="数据时间异常或超过适用时限",
                field_path=path,
                source_refs=[fact.source],
                details={
                    "observed_at": fact.observed_at.isoformat(),
                    "age_days": age,
                    "max_age_days": requirement.max_age_days,
                },
            )
    return fact, None
