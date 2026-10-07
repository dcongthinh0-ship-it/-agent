from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from chemo_agent_product.config import Settings
from chemo_agent_product.demo import DATABASE, create_demo_app, fresh_demo_token
from chemo_agent_product.demo_fixtures import DOCTORS, stamp
from chemo_agent_product.demo_hospital import DemoHospital
from chemo_agent_product.hospital_contracts import RESPONSE_CONTRACTS
from chemo_agent_product.security import BusinessError, Principal, verify_test_token


def payload(patient="DEMO_P001"):
    return {
        "patient_regimen_record_id": "DEMO_RECORD_" + patient,
        "patient_id": patient,
        "encounter_id": "DEMO_E" + patient[-3:],
        "doctor_id": "DEMO_DOC01",
        "dept_code": "DEMO_ONC",
        "confirmed_regimen": {
            "regimen_name": "虚构演示方案",
            "decision_status": "CONFIRMED",
            "confirmed_by": "DEMO_DOC01",
            "confirmed_time": "20261008090000000",
        },
        "orders": [
            {
                "line_no": i,
                "order_category": "MAIN_TREATMENT",
                "item_type": "DRUG",
                "drug_code": "DEMO_DRUG_" + str(i),
                "drug_name": "演示药" + str(i),
                "dose_value": "10",
                "dose_unit": "mg",
                "quantity": 1,
                "quantity_unit": "演示包装",
                "route_code": "DEMO_IVPB",
                "frequency_code": "DEMO_ONCE",
                "start_day": "1",
                "long_term_flag": "N",
            }
            for i in range(1, 5)
        ],
    }


def invoke(hospital, operation, body):
    result = hospital.invoke(operation, body)
    RESPONSE_CONTRACTS[operation].model_validate(result)
    return result


def submit(hospital, body):
    invoke(hospital, "B_ValidateChemoOrders", body)
    imported = {
        **body,
        "idempotency_key": "DEMO_IMPORT",
        "visit_type": "INPATIENT",
        "regimen_version": "1",
        "data_version": "1",
        "planned_start_time": "20261008090000000",
    }
    result = invoke(hospital, "B_ImportChemoOrders", imported)
    assert result["handover_status"] == "ACCEPTED"
    return imported, result


def test_persistent_idempotent_full_delivery_and_separate_signature(tmp_path, monkeypatch):
    path = tmp_path / "hospital.sqlite3"
    hospital = DemoHospital(path)
    body = payload()
    imported, original = submit(hospital, body)
    hospital = DemoHospital(path)
    assert invoke(hospital, "B_ImportChemoOrders", imported) == original
    changed = deepcopy(imported)
    changed["orders"][0]["dose_value"] = "11"
    with pytest.raises(BusinessError, match="IDEMPOTENCY_CONFLICT"):
        hospital.invoke("B_ImportChemoOrders", changed)
    query = {"patient_regimen_record_id": body["patient_regimen_record_id"]}
    assert invoke(hospital, "Q_GetRegimenHandoverStatus", query)["handover_status"] == "PROCESSED"
    archive = {k: v for k, v in body.items() if k not in {"doctor_id", "dept_code"}}
    result = invoke(
        hospital,
        "B_ArchiveRegimenRecord",
        {
            **archive,
            "idempotency_key": "DEMO_ARCHIVE",
            "document_type": "DEMO_CHEMO",
            **DOCTORS,
        },
    )
    assert result["signature_status"] == "UNSIGNED"
    result = invoke(hospital, "Q_GetRegimenArchiveStatus", query)
    assert result["signature_status"] == "SIGNED"
    assert result["responsible_doctor_id"] != result["attending_physician_id"]
    monkeypatch.setattr("chemo_agent_product.demo_hospital.stamp", lambda: "20261009100000000")
    repeated = invoke(DemoHospital(path), "Q_GetRegimenArchiveStatus", query)
    assert repeated["signature_time"] == result["signature_time"]
    assert repeated["signature_id"] == result["signature_id"]
    assert hospital.summary(query["patient_regimen_record_id"])["verification"]["fully_processed"]
    with pytest.raises(BusinessError, match="CANCEL_NOT_ALLOWED"):
        hospital.invoke(
            "B_CancelRegimenHandover",
            {
                **query,
                "idempotency_key": "DEMO_CANCEL",
                "cancel_reason": "演示撤销",
            },
        )


def test_partial_failure_cannot_archive_and_can_cancel(tmp_path):
    hospital = DemoHospital(tmp_path / "hospital.sqlite3")
    body = payload("DEMO_P004")
    submit(hospital, body)
    query = {"patient_regimen_record_id": body["patient_regimen_record_id"]}
    result = invoke(hospital, "Q_GetRegimenHandoverStatus", query)
    assert result["handover_status"] == "PARTIAL_PROCESSED"
    assert result["line_results"][1]["status"] == "FAILED"
    archive = {k: v for k, v in body.items() if k not in {"doctor_id", "dept_code"}}
    with pytest.raises(BusinessError, match="HANDOVER_NOT_COMPLETE"):
        hospital.invoke(
            "B_ArchiveRegimenRecord",
            {
                **archive,
                "idempotency_key": "DEMO_ARCHIVE",
                "document_type": "DEMO_CHEMO",
            },
        )
    assert not hospital.summary(query["patient_regimen_record_id"])["verification"][
        "fully_processed"
    ]
    assert (
        invoke(
            hospital,
            "B_CancelRegimenHandover",
            {
                **query,
                "idempotency_key": "DEMO_CANCEL",
                "cancel_reason": "演示撤销",
            },
        )["handover_status"]
        == "CANCELLED"
    )


def test_demo_never_writes_another_database(tmp_path):
    with pytest.raises(RuntimeError, match="different database"):
        create_demo_app(
            Settings(),
            {"database_url": "postgresql://localhost/real_patient_db"},
            tmp_path,
            "http://127.0.0.1:5174",
        )


def test_hospital_timestamp_is_shanghai_local_time():
    assert stamp(datetime(2026, 10, 8, 1, 2, 3, tzinfo=UTC)) == "20261008090203000"


def test_demo_frontend_cannot_receive_credentials_off_loopback(tmp_path):
    with pytest.raises(RuntimeError, match="frontend must be loopback-only"):
        create_demo_app(
            Settings(),
            {
                "database_url": "postgresql://127.0.0.1/" + DATABASE,
                "api_origin": "http://127.0.0.1:8012",
            },
            tmp_path,
            "https://outside.example",
        )


def test_demo_credentials_can_renew_after_the_server_runs_overnight():
    actor = Principal(
        subject="demo-workstation",
        hospital_id=uuid4(),
        staff_id=uuid4(),
        roles=["DOCTOR"],
        expires_at=1,
    )
    key = "unit-test-only-signing-key-over-thirty-two-characters"
    renewed = verify_test_token(fresh_demo_token(actor, key), key, "test")
    assert renewed.hospital_id == actor.hospital_id and renewed.staff_id == actor.staff_id
    assert renewed.roles == actor.roles
    assert actor.expires_at == 1
