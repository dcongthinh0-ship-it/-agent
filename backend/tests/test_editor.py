from datetime import UTC, datetime
from uuid import uuid4

import pytest

from chemo_agent_product.domain import PatientSnapshot
from chemo_agent_product.editor import compile_revision
from chemo_agent_product.patient_contracts import SaveInput
from chemo_agent_product.security import BusinessError


def template():
    return {
        "version_id": str(uuid4()),
        "fields": [
            {
                "field_key": "patient_name",
                "label": "姓名",
                "value_type": "string",
                "edit_policy": "HIS_BOUND_READONLY",
                "required": False,
            },
            {
                "field_key": "current_cycle",
                "label": "当前周期",
                "value_type": "integer",
                "edit_policy": "RUNTIME_EDITABLE",
                "required": True,
            },
            {
                "field_key": "treatment_date",
                "label": "日期",
                "value_type": "date",
                "edit_policy": "RUNTIME_EDITABLE",
                "required": True,
            },
        ],
        "medications": [
            {
                "item_key": "M1",
                "source_drug_name": "合同用药",
                "standard_dose_text": "10mg",
                "administration_day_text": "第1天",
                "frequency_text": None,
            }
        ],
    }


def patient():
    return PatientSnapshot(
        patient_ref="CONTRACT_ONLY",
        encounter_ref="CONTRACT_ONLY",
        captured_at=datetime.now(UTC),
        facts={},
    )


def test_readonly_fields_and_unknown_drugs_are_rejected():
    for kwargs in [
        {"field_values": {"patient_name": "altered"}},
        {"medication_values": {"UNKNOWN": {"actual_dose_text": "1"}}},
    ]:
        with pytest.raises(BusinessError):
            compile_revision(template(), patient(), SaveInput(expected_row_version=1, **kwargs), {})


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "nan"])
def test_cycle_invalid(value):
    with pytest.raises(BusinessError):
        compile_revision(
            template(),
            patient(),
            SaveInput(expected_row_version=1, field_values={"current_cycle": value}),
            {},
        )


def test_unknown_hospital_codes_never_become_ready():
    result = compile_revision(
        template(),
        patient(),
        SaveInput(
            expected_row_version=1,
            field_values={"current_cycle": 1, "treatment_date": "2026-10-07"},
            medication_values={"M1": {"actual_dose_text": "10mg"}},
        ),
        {"patient_name": "contract"},
    )
    assert result["orders"][0]["resolution_state"] == "UNMAPPED"
    assert "hospital_item_code" not in result["orders"][0]
    assert result["fields"]["patient_name"] == "contract"
    assert result["manifest"]["reference_doses"]["M1"]["state"] == "NOT_COMPUTABLE"


@pytest.mark.parametrize("dose", ["0", "0 mg", "0.0mg", "-5 mg", "-.5 mg", "NaN mg", "inf"])
def test_nonpositive_dose_with_units_is_rejected(dose):
    with pytest.raises(BusinessError, match="DOSE_INVALID"):
        compile_revision(
            template(),
            patient(),
            SaveInput(expected_row_version=1, medication_values={"M1": {"actual_dose_text": dose}}),
            {},
        )


@pytest.mark.parametrize("dose", ["10 mg", ".5 mg", "100-150mg", "10mg/日"])
def test_positive_dose_text_is_preserved(dose):
    result = compile_revision(
        template(),
        patient(),
        SaveInput(expected_row_version=1, medication_values={"M1": {"actual_dose_text": dose}}),
        {},
    )
    assert result["orders"][0]["dose_text_raw"] == dose
