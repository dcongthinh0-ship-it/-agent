from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from chemo_agent_product.core.domain import Contract
from chemo_agent_product.modules.recommendation.schemas import PreparedCandidate


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


class LaunchInput(Contract):
    patient_id: str = Field(min_length=1, max_length=128)
    encounter_id: str = Field(min_length=1, max_length=128)
    operator_id: str = Field(min_length=1, max_length=128)
    operator_name: str = Field(min_length=1, max_length=128)
    dept_code: str = Field(min_length=1, max_length=128)
    operator_dept_name: str = Field(min_length=1, max_length=128)
    request_scene: Literal["AUTO_PREPARE", "ORDER_CONTEXT", "ASSISTANT_OPEN"] = "AUTO_PREPARE"
    session_scope_ref: str = Field(min_length=1, max_length=128)
    client_generation: int | None = Field(default=None, ge=1, le=9223372036854775807)
    previous_context_id: UUID | None = None


class RefreshInput(Contract):
    expected_generation: int = Field(ge=1)
