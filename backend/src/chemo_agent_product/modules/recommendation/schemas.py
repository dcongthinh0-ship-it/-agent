from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from chemo_agent_product.core.domain import Contract


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


class ActionInput(Contract):
    kind: Literal["VIEW", "COMPARE"]
    candidate_ids: list[UUID] = Field(min_length=1, max_length=3)


class CompareInput(Contract):
    candidate_ids: list[UUID] = Field(min_length=2, max_length=3)
