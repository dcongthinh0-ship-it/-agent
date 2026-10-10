"""Normative sections 4.1 of the final v1.0.1 document, not its incomplete examples.

Hospital codes are opaque validated strings. These models define payload contracts;
they do not assert that a hospital route, identity or mapping has been approved.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, model_validator

from chemo_agent_product.core.domain import Contract

Code = Annotated[str, Field(min_length=1, max_length=256, pattern=r".*\S.*")]


def valid_stamp(value: str) -> str:
    datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    return value


Stamp = Annotated[str, Field(pattern=r"^\d{17}$"), AfterValidator(valid_stamp)]
Positive = Annotated[Decimal, Field(gt=0)]


def positive_text(value: str) -> str:
    if Decimal(value) <= 0:
        raise ValueError("must be positive")
    return value


PositiveText = Annotated[str, Field(pattern=r"^\d+(?:\.\d+)?$"), AfterValidator(positive_text)]
DayText = Annotated[str, Field(pattern=r"^[1-9]\d*$")]
HandoverStatus = Literal[
    "ACCEPTED",
    "PROCESSING",
    "PROCESSED",
    "PARTIAL_PROCESSED",
    "REJECTED",
    "CANCELLED",
    "PARTIAL_CANCELLED",
    "FAILED",
]
LineStatus = Literal["ACCEPTED", "PROCESSING", "PROCESSED", "REJECTED", "CANCELLED", "FAILED"]


class ConfirmedRegimen(Contract):
    regimen_name: Code
    decision_status: Literal["CONFIRMED"]
    confirmed_by: Code
    confirmed_time: Stamp
    regimen_code: Code | None = None
    regimen_cycle_no: int | None = Field(default=None, ge=1)
    regimen_total_cycles: int | None = Field(default=None, ge=1)
    treatment_purpose: str | None = None
    clinical_snapshot_reference: Code | None = None


class HospitalOrder(Contract):
    line_no: int = Field(ge=1)
    group_no: int | None = Field(default=None, ge=1)
    order_category: Literal["PREMEDICATION", "MAIN_TREATMENT", "SUPPORTIVE", "HYDRATION", "FLUSH"]
    item_type: Literal["DRUG", "DILUENT", "CLINICAL_ITEM"]
    clinical_item_code: Code | None = None
    clinical_item_name: Code | None = None
    drug_code: Code | None = None
    drug_name: Code | None = None
    specification: str | None = None
    dosage_form: str | None = None
    dose_value: PositiveText
    dose_unit: Code
    quantity: Positive
    quantity_unit: Code
    route_code: Code
    frequency_code: Code
    start_day: DayText
    end_day: DayText | None = None
    long_term_flag: Literal["Y", "N"]
    diluent_code: Code | None = None
    diluent_name: Code | None = None
    diluent_volume_ml: Positive | None = None
    infusion_duration_min: Positive | None = None
    infusion_rate: PositiveText | None = None
    rate_unit: Code | None = None
    method: Code | None = None
    execution_dept_code: Code | None = None
    remark: str | None = None
    special_instructions: str | None = None

    @model_validator(mode="after")
    def required_item_codes(self):
        if self.item_type == "CLINICAL_ITEM":
            if not self.clinical_item_code or not self.clinical_item_name:
                raise ValueError("clinical item code and name are required")
        elif not self.drug_code or not self.drug_name:
            raise ValueError("hospital drug code and name are required")
        if self.end_day and int(self.end_day) < int(self.start_day):
            raise ValueError("end day precedes start day")
        return self


class ChangeLog(Contract):
    field_name: Code
    before_value: Any = None
    after_value: Any
    reason: str | None = None
    changed_by: Code
    changed_time: Stamp


class PatientRegimenPayload(Contract):
    patient_regimen_record_id: Code
    patient_id: Code
    encounter_id: Code
    confirmed_regimen: ConfirmedRegimen
    orders: list[HospitalOrder] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_lines(self):
        if len({item.line_no for item in self.orders}) != len(self.orders):
            raise ValueError("duplicate order line")
        return self


class ValidationRequest(PatientRegimenPayload):
    doctor_id: Code
    dept_code: Code

    @model_validator(mode="after")
    def same_confirming_doctor(self):
        if self.doctor_id != self.confirmed_regimen.confirmed_by:
            raise ValueError("confirming doctor does not match request doctor")
        return self


class ImportRequest(ValidationRequest):
    idempotency_key: Code
    visit_type: Literal["OUTPATIENT", "INPATIENT"]
    regimen_version: Code
    data_version: Code
    planned_start_time: Stamp
    planned_end_time: Stamp | None = None
    execution_dept_code: Code | None = None
    remark: str | None = None
    change_log: list[ChangeLog] = Field(default_factory=list)


class ValidationResult(Contract):
    rule_code: Code
    rule_name: Code
    validation_status: Literal["PASSED", "WARNING", "BLOCKED"]
    message: str
    severity: Literal["INFO", "WARNING", "ERROR"] | None = None
    target_line_no: int | None = Field(default=None, ge=1)
    field_name: str | None = None
    suggestion: str | None = None


class ValidationResponse(Contract):
    validation_status: Literal["PASSED", "WARNING", "BLOCKED"]
    validation_results: list[ValidationResult]
    validated_time: Stamp


class LineResult(Contract):
    line_no: int = Field(ge=1)
    status: LineStatus
    his_order_no: Code | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool | None = None


class HandoverResponse(Contract):
    handover_status: HandoverStatus
    processed_time: Stamp
    line_results: list[LineResult] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    hospital_business_ref: Code | None = None
    # Native dictionaries must be separately configured, never guessed from these examples.
    order_draft_status: str | None = None
    execution_status: str | None = None
    message: str | None = None


class ImportResponse(HandoverResponse):
    line_results: list[LineResult] = Field(min_length=1)


class CancelResponse(HandoverResponse):
    handover_status: Literal["CANCELLED", "PARTIAL_CANCELLED", "FAILED"]


class HandoverQuery(Contract):
    patient_regimen_record_id: Code | None = None
    hospital_business_ref: Code | None = None

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.patient_regimen_record_id and not self.hospital_business_ref:
            raise ValueError("a confirmed record or hospital business reference is required")
        return self


class CancelRequest(Contract):
    patient_regimen_record_id: Code
    idempotency_key: Code
    cancel_reason: Code
    hospital_business_ref: Code | None = None
    line_no_list: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def unique_selected_lines(self):
        if self.line_no_list and len(self.line_no_list) != len(set(self.line_no_list)):
            raise ValueError("duplicate cancellation line")
        return self


class ArchiveAttachment(Contract):
    attachment_id: Code | None = None
    attachment_name: str | None = None
    attachment_type: str | None = None
    attachment_url: str | None = None


class ArchiveRequest(PatientRegimenPayload):
    idempotency_key: Code
    document_type: Code
    responsible_doctor_id: Code | None = None
    responsible_doctor_name: str | None = None
    attending_physician_id: Code | None = None
    attending_physician_name: str | None = None
    deputy_chief_physician_id: Code | None = None
    deputy_chief_physician_name: str | None = None
    chief_physician_id: Code | None = None
    chief_physician_name: str | None = None
    doctor_phone: str | None = None
    hospital_business_ref: Code | None = None
    handover_status: HandoverStatus | None = None
    line_results: list[LineResult] = Field(default_factory=list)
    change_log: list[ChangeLog] = Field(default_factory=list)
    attachment_list: list[ArchiveAttachment] = Field(default_factory=list)


class ArchiveQuery(Contract):
    patient_regimen_record_id: Code
    document_id: Code | None = None


class ArchiveResponse(Contract):
    document_id: Code
    archive_status: Literal["ARCHIVED", "PROCESSING", "FAILED"]
    processed_time: Stamp
    archive_error: dict[str, Any] | None = None
    signature_id: Code | None = None
    signature_status: Literal["UNSIGNED", "SIGNED", "FAILED", "NOT_APPLICABLE"] | None = None
    signature_time: Stamp | None = None
    message: str | None = None


class ArchiveStatusResponse(Contract):
    patient_regimen_record_id: Code
    document_id: Code | None = None
    archive_status: Literal["ARCHIVED", "PROCESSING", "FAILED"]
    processed_time: Stamp
    archive_error: dict[str, Any] | None = None
    responsible_doctor_id: Code | None = None
    responsible_doctor_name: str | None = None
    attending_physician_id: Code | None = None
    attending_physician_name: str | None = None
    deputy_chief_physician_id: Code | None = None
    deputy_chief_physician_name: str | None = None
    chief_physician_id: Code | None = None
    chief_physician_name: str | None = None
    doctor_phone: str | None = None
    signature_id: Code | None = None
    signature_status: Literal["UNSIGNED", "SIGNED", "FAILED", "NOT_APPLICABLE"]
    signature_time: Stamp | None = None


REQUEST_CONTRACTS = {
    "B_ValidateChemoOrders": ValidationRequest,
    "B_ImportChemoOrders": ImportRequest,
    "Q_GetRegimenHandoverStatus": HandoverQuery,
    "B_CancelRegimenHandover": CancelRequest,
    "B_ArchiveRegimenRecord": ArchiveRequest,
    "Q_GetRegimenArchiveStatus": ArchiveQuery,
}

RESPONSE_CONTRACTS = {
    "B_ValidateChemoOrders": ValidationResponse,
    "B_ImportChemoOrders": ImportResponse,
    "Q_GetRegimenHandoverStatus": HandoverResponse,
    "B_CancelRegimenHandover": CancelResponse,
    "B_ArchiveRegimenRecord": ArchiveResponse,
    "Q_GetRegimenArchiveStatus": ArchiveStatusResponse,
}


def reconcile_receipt(receipt: HandoverResponse, submitted_lines: set[int]) -> dict:
    """A receipt can cover fewer lines than submitted; that is never full completion."""
    actual = [line.line_no for line in receipt.line_results]
    if len(actual) != len(set(actual)) or not set(actual) <= submitted_lines:
        raise ValueError("receipt contains duplicate or unrelated order lines")
    complete = set(actual) == submitted_lines and all(
        line.status == "PROCESSED" for line in receipt.line_results
    )
    return {
        "external_status": receipt.handover_status,
        "coverage": "VERIFIED" if set(actual) == submitted_lines else "PARTIAL",
        "fully_processed": receipt.handover_status == "PROCESSED" and complete,
        "query_before_retry": receipt.handover_status in {"ACCEPTED", "PROCESSING"},
        "retryable_lines": [
            line.line_no
            for line in receipt.line_results
            if line.status in {"FAILED", "REJECTED"} and line.retryable is True
        ],
    }
