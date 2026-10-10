from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel


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
