from copy import deepcopy

import pytest
from pydantic import ValidationError

from chemo_agent_product.integrations.hospital.contracts import (
    ArchiveRequest,
    HandoverQuery,
    HandoverResponse,
    ImportRequest,
    ValidationRequest,
    reconcile_receipt,
)


def payload():
    # Contract-only values. No hospital mapping is being asserted.
    return {
        "patient_regimen_record_id": "CONTRACT_RECORD",
        "patient_id": "CONTRACT_PATIENT",
        "encounter_id": "CONTRACT_ENCOUNTER",
        "doctor_id": "CONTRACT_DOCTOR",
        "dept_code": "CONTRACT_DEPT",
        "confirmed_regimen": {
            "regimen_name": "合同测试方案",
            "decision_status": "CONFIRMED",
            "confirmed_by": "CONTRACT_DOCTOR",
            "confirmed_time": "20261007100000000",
        },
        "orders": [
            {
                "line_no": 1,
                "order_category": "MAIN_TREATMENT",
                "item_type": "DRUG",
                "drug_code": "CONTRACT_DRUG",
                "drug_name": "合同测试药品",
                "dose_value": "10",
                "dose_unit": "mg",
                "quantity": 1,
                "quantity_unit": "CONTRACT_UNIT",
                "route_code": "CONTRACT_ROUTE",
                "frequency_code": "CONTRACT_FREQUENCY",
                "start_day": "1",
                "long_term_flag": "N",
            }
        ],
    }


@pytest.mark.parametrize(
    "field", ["quantity", "quantity_unit", "route_code", "frequency_code", "long_term_flag"]
)
def test_order_examples_cannot_bypass_normative_required_fields(field):
    data = payload()
    del data["orders"][0][field]
    with pytest.raises(ValidationError):
        ValidationRequest.model_validate(data)


def test_import_needs_versions_visit_type_d1_anchor_and_idempotency():
    with pytest.raises(ValidationError):
        ImportRequest.model_validate(payload())
    valid = {
        **payload(),
        "idempotency_key": "CONTRACT_IDEM",
        "visit_type": "OUTPATIENT",
        "regimen_version": "1",
        "data_version": "1",
        "planned_start_time": "20261008120000000",
    }
    assert ImportRequest.model_validate(valid).orders[0].dose_value == "10"
    bad = deepcopy(valid)
    bad["orders"][0]["order_category"] = "CHEMOTHERAPY"
    with pytest.raises(ValidationError):
        ImportRequest.model_validate(bad)
    bad = deepcopy(valid)
    bad["planned_start_time"] = "20261308120000000"
    with pytest.raises(ValidationError):
        ImportRequest.model_validate(bad)


def test_duplicate_lines_mismatched_doctor_and_missing_hospital_code_rejected():
    data = payload()
    data["orders"] *= 2
    with pytest.raises(ValidationError):
        ValidationRequest.model_validate(data)
    data = payload()
    data["doctor_id"] = "OTHER_DOCTOR"
    with pytest.raises(ValidationError):
        ValidationRequest.model_validate(data)
    data = payload()
    data["orders"][0]["drug_code"] = " "
    with pytest.raises(ValidationError):
        ValidationRequest.model_validate(data)


def test_archive_uses_its_own_required_fields_and_keeps_physician_roles_distinct():
    data = payload()
    del data["doctor_id"], data["dept_code"]
    archive = ArchiveRequest.model_validate(
        {
            **data,
            "idempotency_key": "CONTRACT_ARCHIVE",
            "document_type": "CHEMO_REGIMEN_RECORD",
            "responsible_doctor_id": "CONTRACT_RESPONSIBLE",
            "attending_physician_id": "CONTRACT_ATTENDING",
            "chief_physician_id": "CONTRACT_CHIEF",
        }
    )
    assert archive.responsible_doctor_id != archive.attending_physician_id
    assert archive.confirmed_regimen.confirmed_by == "CONTRACT_DOCTOR"
    with pytest.raises(ValidationError):
        HandoverQuery.model_validate({})


def test_partial_receipt_is_not_full_success_and_retry_only_uses_explicit_retryable_lines():
    receipt = HandoverResponse.model_validate(
        {
            "handover_status": "PROCESSED",
            "processed_time": "20261008120000000",
            "line_results": [{"line_no": 1, "status": "PROCESSED"}],
        }
    )
    assert not reconcile_receipt(receipt, {1, 2})["fully_processed"]
    with pytest.raises(ValueError):
        reconcile_receipt(receipt, {2})
    other = receipt.model_copy(update={"line_results": receipt.line_results * 2})
    with pytest.raises(ValueError):
        reconcile_receipt(other, {1})
    timed = HandoverResponse.model_validate(
        {"handover_status": "PROCESSING", "processed_time": "20261008120000000", "line_results": []}
    )
    assert reconcile_receipt(timed, {1})["query_before_retry"]
