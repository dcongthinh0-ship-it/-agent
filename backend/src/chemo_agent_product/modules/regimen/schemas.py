from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel


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
