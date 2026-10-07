from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CapabilityStatus(BaseModel):
    product: Literal["化疗智能体"] = "化疗智能体"
    mode: Literal["READ_ONLY_TEST", "WORKFLOW_TEST", "NOT_APPROVED"]
    database: Literal["CONNECTED", "UNCONFIGURED", "UNAVAILABLE", "TEST_DOUBLE"]
    model: Literal["NOT_CONNECTED", "CONFIGURED_NOT_VERIFIED"] = "NOT_CONNECTED"
    hospital: Literal["NOT_CONNECTED", "CONFIGURED_NOT_VERIFIED"] = "NOT_CONNECTED"
    patient_context: Literal["NOT_CONNECTED", "TEST_ONLY"] = "NOT_CONNECTED"
    clinical_release: Literal["NOT_ENABLED"] = "NOT_ENABLED"


class RegimenSummary(BaseModel):
    regimen_id: UUID
    regimen_code: str
    display_name: str
    cancer_category: str | None
    version_id: UUID
    version_no: int
    version_status: str
    evidence_link_count: int


class RegimenPage(BaseModel):
    items: list[RegimenSummary]
    total: int
    page: int
    page_size: int


class MedicationItem(BaseModel):
    item_key: str
    display_order: int
    source_drug_name: str
    generic_name: str | None
    standard_dose_text: str | None
    dose_unit: str | None
    dose_basis: str | None
    route_text: str | None
    frequency_text: str | None
    administration_day_text: str | None


class FieldDefinition(BaseModel):
    field_key: str
    label: str
    value_type: str
    widget_type: str | None
    source_type: str
    edit_policy: str
    required: bool
    repeatable: bool
    display_order: int
    default_value: Any | None = None


class ContentBlock(BaseModel):
    section_code: str
    block_type: str
    display_order: int
    title: str | None
    raw_text: str | None


class RegimenDetail(BaseModel):
    regimen_id: UUID
    regimen_code: str
    display_name: str
    cancer_category: str | None
    version_id: UUID
    version_no: int
    version_status: str
    blueprint_schema_version: str | None
    document_tree: dict[str, Any]
    fields: list[FieldDefinition]
    medications: list[MedicationItem]
    content_blocks: list[ContentBlock]
    content_truncated: bool = False
    word_layout: dict[str, Any] | None = None
    layout_verification: dict[str, Any] | None = None


class EvidenceSummary(BaseModel):
    association_id: UUID
    association_scope: str
    association_status: str
    evidence_id: UUID
    source_code: str
    display_source: str
    evidence_status: str
    source_title: str | None
    source_version: str | None
    source_status: str | None
    source_recommendation_raw: str | None
    source_evidence_category_raw: str | None
    internal_level: int | None
    evidence_grade: str | None
    grade_mapping_version: str | None
    excerpt_preview: str | None


class EvidenceList(BaseModel):
    regimen_version_id: UUID
    items: list[EvidenceSummary]
    truncated: bool = False


class EvidenceDetail(BaseModel):
    evidence_id: UUID
    source_code: str
    display_source: str
    evidence_status: str
    source_title: str | None
    source_version: str | None
    source_status: str | None
    source_recommendation_raw: str | None
    source_evidence_category_raw: str | None
    internal_level: int | None
    evidence_grade: str | None
    grade_mapping_version: str | None
    verbatim_excerpt: str | None
    source_locator: dict[str, Any]
    disease_scope: dict[str, Any]
    context_description: dict[str, Any]
    source_url: str | None
    source_date: str | None


class ApiError(BaseModel):
    code: str
    message: str
    detail: str | None = Field(default=None)


class PreparedCandidate(BaseModel):
    candidate_id: UUID
    regimen_id: str | None
    regimen_code: str | None
    display_name: str | None
    version_id: str | None
    version_status: str | None
    presentation_region: Literal["RECOMMENDATION", "X_EXCLUDED"]
    rank_group: int | None
    evidence_state: str
    evidence_level: int | None
    evidence_grade: str | None
    data_labels: list[Any]
    safety_labels: list[Any]
    x_reason_code: str | None


class ContextReadout(BaseModel):
    context_id: UUID
    mode: Literal["TEST_ONLY"] = "TEST_ONLY"
    context_state: str
    expires_at: str
    prepare_run_id: UUID | None
    prepare_status: str | None
    prepare_stage: str | None
    prepare_error_code: str | None
    decision_run_id: UUID | None
    decision_status: str | None
    outcome_code: str | None
    patient_ref: str
    encounter_ref: str
    candidates: list[PreparedCandidate]
