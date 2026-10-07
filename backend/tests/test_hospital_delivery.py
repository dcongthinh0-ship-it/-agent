import json

import httpx
import pytest

from chemo_agent_product.hospital_delivery import ConfiguredHospitalDelivery
from chemo_agent_product.security import BusinessError


def driver(tmp_path, transport, operations):
    path = tmp_path / "delivery.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": "v1.0.1-CONTRACT-TEST",
                "credential_environment_variable": "CONTRACT_TEST_CREDENTIAL",
                "operations": {
                    name: {"url": "https://contract.invalid/" + name} for name in operations
                },
            }
        )
    )
    return ConfiguredHospitalDelivery(str(path), transport)


@pytest.mark.asyncio
async def test_unconfigured_delivery_never_calls_transport():
    with pytest.raises(BusinessError, match="HOSPITAL_DELIVERY_NOT_CONFIGURED"):
        await ConfiguredHospitalDelivery(None).invoke(
            "Q_GetRegimenArchiveStatus", {}, "CONTRACT_DOCTOR", observer=None
        )


@pytest.mark.asyncio
async def test_timeout_audits_unknown_outcome_once_and_never_automatically_retries(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONTRACT_TEST_CREDENTIAL", "CONTRACT-ONLY")
    calls, audit = [], []

    async def transport(request):
        calls.append(request)
        raise httpx.ReadTimeout("contract-only timeout", request=request)

    async def observer(event):
        audit.append(event)

    delivery = driver(tmp_path, httpx.MockTransport(transport), ["B_CancelRegimenHandover"])
    with pytest.raises(BusinessError) as error:
        await delivery.invoke(
            "B_CancelRegimenHandover",
            {
                "patient_regimen_record_id": "CONTRACT_RECORD",
                "idempotency_key": "CONTRACT_IDEM",
                "cancel_reason": "contract-only",
            },
            "CONTRACT_DOCTOR",
            observer=observer,
        )
    assert error.value.code == "DELIVERY_OUTCOME_UNKNOWN"
    assert len(calls) == 1
    assert [event["phase"] for event in audit] == ["STARTED", "COMPLETED"]
    assert audit[-1]["transport_outcome"] == "TIMEOUT"
    assert "CONTRACT-ONLY" not in str(audit)


@pytest.mark.asyncio
async def test_archive_query_requires_signature_and_rejects_another_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTRACT_TEST_CREDENTIAL", "CONTRACT-ONLY")
    response = {
        "patient_regimen_record_id": "CONTRACT_RECORD",
        "archive_status": "PROCESSING",
        "processed_time": "20261007120000000",
    }
    calls = []

    async def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "0", "data": {"content": [response]}})

    delivery = driver(tmp_path, httpx.MockTransport(transport), ["Q_GetRegimenArchiveStatus"])
    payload = {"patient_regimen_record_id": "CONTRACT_RECORD"}
    with pytest.raises(BusinessError) as error:
        await delivery.invoke(
            "Q_GetRegimenArchiveStatus", payload, "CONTRACT_DOCTOR", observer=None
        )
    assert error.value.code == "DELIVERY_RECEIPT_INVALID"
    response["signature_status"] = "UNSIGNED"
    valid, _ = await delivery.invoke(
        "Q_GetRegimenArchiveStatus", payload, "CONTRACT_DOCTOR", observer=None
    )
    assert valid.archive_status == "PROCESSING" and valid.document_id is None
    response["patient_regimen_record_id"] = "OTHER_RECORD"
    with pytest.raises(BusinessError) as error:
        await delivery.invoke(
            "Q_GetRegimenArchiveStatus", payload, "CONTRACT_DOCTOR", observer=None
        )
    assert error.value.code == "DELIVERY_RECORD_MISMATCH"
    assert len(calls) == 3
