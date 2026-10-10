from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CapabilityStatus(BaseModel):
    product: Literal["化疗智能体"] = "化疗智能体"
    mode: Literal["READ_ONLY_TEST", "WORKFLOW_TEST", "NOT_APPROVED"]
    database: Literal["CONNECTED", "UNCONFIGURED", "UNAVAILABLE", "TEST_DOUBLE"]
    model: Literal["NOT_CONNECTED", "CONFIGURED_NOT_VERIFIED"] = "NOT_CONNECTED"
    hospital: Literal["NOT_CONNECTED", "CONFIGURED_NOT_VERIFIED"] = "NOT_CONNECTED"
    patient_context: Literal["NOT_CONNECTED", "TEST_ONLY"] = "NOT_CONNECTED"
    clinical_release: Literal["NOT_ENABLED"] = "NOT_ENABLED"
    demo_mode: bool = False


class ApiError(BaseModel):
    code: str
    message: str
    detail: str | None = Field(default=None)
