from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from chemo_agent_product.domain import Contract


class LaunchInput(Contract):
    patient_id: str = Field(min_length=1, max_length=128)
    encounter_id: str = Field(min_length=1, max_length=128)
    operator_id: str = Field(min_length=1, max_length=128)
    operator_name: str = Field(min_length=1, max_length=128)
    dept_code: str = Field(min_length=1, max_length=128)
    operator_dept_name: str = Field(min_length=1, max_length=128)
    request_scene: Literal["AUTO_PREPARE", "ORDER_CONTEXT", "ASSISTANT_OPEN"] = "AUTO_PREPARE"
    session_scope_ref: str = Field(min_length=1, max_length=128)
    previous_context_id: UUID | None = None


class MedicationEdit(Contract):
    actual_dose_text: str = Field(default="", max_length=200)
    administration_day_text: str = Field(default="", max_length=200)
    instructions: str = Field(default="", max_length=2000)


class SaveInput(Contract):
    expected_row_version: int = Field(ge=1)
    base_revision_id: UUID | None = None
    field_values: dict[str, Any] = Field(default_factory=dict, max_length=500)
    medication_values: dict[str, MedicationEdit] = Field(default_factory=dict, max_length=250)
    change_reason: str | None = Field(default=None, max_length=2000)


class ConfirmInput(Contract):
    revision_id: UUID
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_row_version: int = Field(ge=1)
    acknowledged_codes: list[str] = Field(default_factory=list, max_length=100)


class ActionInput(Contract):
    kind: Literal["VIEW", "COMPARE"]
    candidate_ids: list[UUID] = Field(min_length=1, max_length=3)


class RefreshInput(Contract):
    expected_generation: int = Field(ge=1)


class AgentRequest(Contract):
    kind: Literal["RECOMMENDATION", "REVIEWER"]
    revision_id: UUID | None = None
    question: str = Field(default="", max_length=2000)


class CompareInput(Contract):
    candidate_ids: list[UUID] = Field(min_length=2, max_length=3)
