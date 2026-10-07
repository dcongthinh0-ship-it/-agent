import json
from datetime import UTC, datetime

import httpx
import pytest

from chemo_agent_product.hospital import ConfiguredHospitalReader, pointer
from chemo_agent_product.security import BusinessError


def configure(tmp_path, monkeypatch, identity=True):
    monkeypatch.setenv("CONTRACT_HOSPITAL_TOKEN", "CONTRACT_ONLY")
    path = tmp_path / "adapter.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": "v1.0.1",
                "credential_environment_variable": "CONTRACT_HOSPITAL_TOKEN",
                "operations": {
                    "Q_GetPatientClinicalData": {
                        "url": "http://contract.invalid/read",
                        "patient_pointer": "/data/content/patient_id",
                        "encounter_pointer": "/data/content/encounter_id",
                    }
                },
                "facts": {
                    "weight": {
                        "operation": "Q_GetPatientClinicalData",
                        "pointer": "/data/content/weight",
                        "unit_pointer": "/data/content/unit",
                        "observed_at_pointer": "/data/content/measured",
                    }
                },
            }
        )
    )

    def respond(request):
        assert request.headers["operator-id"] == "CONTRACT_DOCTOR"
        assert len(request.headers["timestamp"]) == 17
        assert request.headers["request-id"]
        assert json.loads(request.content) == {
            "patient_id": "CONTRACT_PATIENT",
            "encounter_id": "CONTRACT_ENCOUNTER",
        }
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "content": {
                        "patient_id": "CONTRACT_PATIENT" if identity else "OTHER",
                        "encounter_id": "CONTRACT_ENCOUNTER",
                        "weight": 60,
                        "unit": "kg",
                        "measured": datetime.now(UTC).isoformat(),
                    }
                },
            },
        )

    return ConfiguredHospitalReader(str(path), httpx.MockTransport(respond))


@pytest.mark.asyncio
async def test_exact_request_headers_identity_and_source_locator(tmp_path, monkeypatch):
    adapter = configure(tmp_path, monkeypatch)
    sources = await adapter.fetch("CONTRACT_PATIENT", "CONTRACT_ENCOUNTER", "CONTRACT_DOCTOR")
    snapshot = adapter.normalize(sources, "CONTRACT_PATIENT", "CONTRACT_ENCOUNTER")
    assert snapshot.facts["weight"].value == 60
    assert snapshot.facts["weight"].source.content_hash == sources[0].content_hash
    assert snapshot.facts["weight"].locator["json_pointer"] == "/data/content/weight"


@pytest.mark.asyncio
async def test_identity_mismatch_stops_before_snapshot(tmp_path, monkeypatch):
    with pytest.raises(BusinessError, match="SOURCE_PATIENT_MISMATCH"):
        await configure(tmp_path, monkeypatch, False).fetch(
            "CONTRACT_PATIENT", "CONTRACT_ENCOUNTER", "CONTRACT_DOCTOR"
        )


def test_json_pointer_handles_missing_and_escaped_keys():
    assert pointer({"a/b": {"~": [1]}}, "/a~1b/~0/0") == 1
    assert pointer({}, "/missing") is None


@pytest.mark.asyncio
async def test_transport_observer_records_response_hash_without_credentials(tmp_path, monkeypatch):
    events = []

    async def observer(event):
        events.append(event)

    await configure(tmp_path, monkeypatch).fetch(
        "CONTRACT_PATIENT", "CONTRACT_ENCOUNTER", "CONTRACT_DOCTOR", observer=observer
    )
    assert [event["phase"] for event in events] == ["STARTED", "COMPLETED"]
    assert events[1]["transport_outcome"] == "RESPONDED"
    assert events[1]["business_code"] == "0" and len(events[1]["response_hash"]) == 64
    assert not any(
        "authorization" in str(event).lower() or "CONTRACT_ONLY" in str(event) for event in events
    )
