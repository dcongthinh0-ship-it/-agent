from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from chemo_agent_product.core.domain import Contract


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
    revision_hash: str = Field(pattern="^[0-9a-f]{64}$")
    expected_row_version: int = Field(ge=1)
    acknowledged_codes: list[str] = Field(default_factory=list, max_length=100)
