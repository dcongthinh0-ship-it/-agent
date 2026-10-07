"""Exercise the running loopback demo through real HTTP; writes only DEMO encounters."""

import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

ORIGIN = "http://127.0.0.1:8012"
report = []
round_id = "_verification_" + str(int(time.time()))


def run_case(patient, delivery=False):
    with httpx.Client(base_url=ORIGIN, timeout=30) as client:
        response = client.post(
            "/demo/token",
            json={
                "patient_id": patient,
                "operator_id": "DEMO_DOC01",
            },
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def call(path, body=None):
            response = (
                client.get(path)
                if body is None
                else client.post(
                    path,
                    json=body,
                    headers={"Idempotency-Key": str(uuid4())},
                )
            )
            if not response.is_success:
                raise RuntimeError(f"{path}: {response.status_code} {response.text[:500]}")
            return response.json()

        launch = call(
            "/api/v1/launch-context",
            {
                "patient_id": patient,
                "encounter_id": "DEMO_E" + patient[-3:] + round_id,
                "operator_id": "DEMO_DOC01",
                "operator_name": "演示主管医师",
                "dept_code": "DEMO_ONC",
                "operator_dept_name": "演示肿瘤科",
                "session_scope_ref": "DEMO_ACCEPTANCE_" + str(uuid4()),
            },
        )
        context = "/api/v1/contexts/" + launch["context_id"]
        deadline = time.monotonic() + 45
        while True:
            status = call(context + "/preparation-status")
            if status["prepare_status"] in {"SUCCEEDED", "FAILED"}:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("patient preparation timed out")
            time.sleep(0.5)
        data = call(context)
        assert status["prepare_status"] == "SUCCEEDED", data
        assert data["patient_ref"] == patient
        assert data["snapshot"]["facts"]["patient_name"]["value"].startswith("演示患者")
        assert len(data["candidates"]) > 0
        result = {
            "patient": patient,
            "candidates": len(data["candidates"]),
            "context_id": launch["context_id"],
        }
        if patient == "DEMO_P003":
            labels = [label["code"] for item in data["candidates"] for label in item["data_labels"]]
            assert "DATA_UNCONFIRMED" in labels and "DATA_EXPIRED" in labels, sorted(set(labels))
            assert data["snapshot"]["facts"].get("weight", {}).get("value") is None
            assert data["snapshot"]["facts"].get("anc", {}).get("value") is None
            result["data_labels"] = sorted(set(labels))
        if not delivery:
            report.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        candidate = next(c for c in data["candidates"] if c["regimen_code"] == "WFAH-BC-001")
        evidence = call(context + "/candidates/" + candidate["candidate_id"] + "/evidence")
        selected = call(context + "/candidates/" + candidate["candidate_id"] + "/select", {})
        iid = selected["instance_id"]
        instance = context + "/instances/" + iid
        demo = "/demo/contexts/" + launch["context_id"] + "/instances/" + iid
        current = call(instance)
        assert current["field_values"]["supervising_physician"] == "演示主管医师"
        assert current["field_values"]["attending_physician"] == "演示主治医师"
        assert current["field_values"]["associate_chief_physician"] == "演示副主任医师"
        assert current["field_values"]["ecog_score"] == 1
        example = call(demo + "/example")
        call(
            instance + "/revisions",
            {
                "expected_row_version": current["row_version"],
                "base_revision_id": None,
                "field_values": example["field_values"],
                "medication_values": example["medication_values"],
            },
        )
        current = call(instance)
        historical = call(instance + "/revisions/" + current["revision_id"])
        assert historical["field_values"]["total_cycles"] == 6
        confirmed = call(
            instance + "/confirm",
            {
                "revision_id": current["revision_id"],
                "revision_hash": current["revision_hash"],
                "expected_row_version": current["row_version"],
                "acknowledged_codes": [item["code"] for item in current["issues"]],
            },
        )
        assert confirmed["reviewer_status"] == "FAILED", confirmed
        review = call(context + "/agent-runs/" + confirmed["reviewer_run_id"])
        assert review["status"] == "FAILED" and review["error_code"] == "MODEL_NOT_CONFIGURED"
        assert call(demo + "/delivery/validate", {})["result"]["validation_status"] == "PASSED"
        imported = call(demo + "/delivery/import", {})
        assert imported["result"]["handover_status"] == "ACCEPTED"
        assert call(demo + "/delivery/import", {})["result"] == imported["result"]
        queried = call(demo + "/delivery/query", {})
        if patient == "DEMO_P004":
            assert queried["result"]["handover_status"] == "PARTIAL_PROCESSED"
            assert not queried["state"]["verification"]["fully_processed"]
            blocked = client.post(demo + "/delivery/archive", json={})
            assert not blocked.is_success
            final = call(demo + "/delivery/cancel", {})
            assert final["state"]["handover_status"] == "CANCELLED"
        else:
            assert queried["state"]["verification"]["fully_processed"]
            archived = call(demo + "/delivery/archive", {})
            assert archived["result"]["signature_status"] == "UNSIGNED"
            final = call(demo + "/delivery/signature", {})
            assert final["state"]["signature_status"] == "SIGNED"
        result.update(
            instance_id=iid,
            revision_id=current["revision_id"],
            evidence_count=len(evidence["items"]),
            model_status="MODEL_NOT_CONFIGURED",
            hospital_status=final["state"]["handover_status"],
            signature_status=final["state"].get("signature_status", "UNSIGNED"),
        )
        report.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    for patient in ("DEMO_P001", "DEMO_P002", "DEMO_P003", "DEMO_P004"):
        run_case(patient, delivery=patient in {"DEMO_P001", "DEMO_P004"})
    target = Path("../artifacts/demo-20261008/http-acceptance.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
