"""Persistent loopback hospital simulation. All codes and receipts are DEMO."""

import json
import sqlite3
from pathlib import Path

from chemo_agent_product.demo_fixtures import DOCTORS, PATIENTS, stamp
from chemo_agent_product.domain import fingerprint
from chemo_agent_product.hospital_contracts import (
    REQUEST_CONTRACTS,
    HandoverResponse,
    reconcile_receipt,
)
from chemo_agent_product.security import BusinessError


class DemoHospital:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "status TEXT NOT NULL, receipt TEXT NOT NULL, archive TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS commands(key TEXT PRIMARY KEY, "
                "hash TEXT NOT NULL, response TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, operation TEXT NOT NULL, "
                "request_hash TEXT NOT NULL, response TEXT NOT NULL, time TEXT NOT NULL)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def invoke(self, name, raw):
        body = (
            REQUEST_CONTRACTS[name].model_validate(raw).model_dump(mode="json", exclude_none=True)
        )
        for key in ("patient_id", "patient_regimen_record_id"):
            if key in body and not body[key].startswith("DEMO_"):
                raise BusinessError("DEMO_SCOPE_ONLY", "模拟医院只接受演示记录", 403)
        record_id = body.get("patient_regimen_record_id")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            command = f"{name}:{body['idempotency_key']}" if body.get("idempotency_key") else None
            old = (
                db.execute("SELECT * FROM commands WHERE key=?", (command,)).fetchone()
                if command
                else None
            )
            if old:
                if old["hash"] != fingerprint(body):
                    raise BusinessError("IDEMPOTENCY_CONFLICT", "同一演示操作标识内容发生变化", 409)
                return json.loads(old["response"])
            row = db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
            if name == "B_ValidateChemoOrders":
                blocked = not all(
                    o.get("drug_code", "").startswith("DEMO_") for o in body["orders"]
                )
                result = {
                    "validation_status": "BLOCKED" if blocked else "PASSED",
                    "validation_results": [
                        {
                            "rule_code": "DEMO_CODES",
                            "rule_name": "演示编码核对",
                            "validation_status": "BLOCKED" if blocked else "PASSED",
                            "message": "仅核对演示载荷结构与编码，不代表真实临床审方",
                        }
                    ],
                    "validated_time": stamp(),
                }
                db.execute(
                    "INSERT OR REPLACE INTO commands VALUES(?,?,?)",
                    ("validation:" + record_id, fingerprint(body["orders"]), json.dumps(result)),
                )
            elif name == "B_ImportChemoOrders":
                validation = db.execute(
                    "SELECT * FROM commands WHERE key=?", ("validation:" + record_id,)
                ).fetchone()
                if (
                    not validation
                    or validation["hash"] != fingerprint(body["orders"])
                    or json.loads(validation["response"])["validation_status"] != "PASSED"
                ):
                    raise BusinessError("VALIDATION_REQUIRED", "请先预校验同一份模拟医嘱", 409)
                if row:
                    raise BusinessError("ALREADY_SUBMITTED", "同一修订已交付，请回查原批次", 409)
                result = {
                    "handover_status": "ACCEPTED",
                    "processed_time": stamp(),
                    "hospital_business_ref": "DEMO_HIS_" + record_id,
                    "line_results": [
                        {"line_no": o["line_no"], "status": "ACCEPTED"} for o in body["orders"]
                    ],
                    "message": "模拟医院已受理，需回查执行结果",
                }
                db.execute(
                    "INSERT INTO records VALUES(?,?,?,?,NULL)",
                    (record_id, json.dumps(body), "ACCEPTED", json.dumps(result)),
                )
            elif name == "Q_GetRegimenHandoverStatus":
                if not row:
                    raise BusinessError("NOT_SUBMITTED", "尚未提交该演示修订", 409)
                result = json.loads(row["receipt"])
                if row["status"] in ("ACCEPTED", "PROCESSING"):
                    submitted = json.loads(row["payload"])
                    partial = PATIENTS[submitted["patient_id"]]["case"] == "partial"
                    result.update(
                        handover_status="PARTIAL_PROCESSED" if partial else "PROCESSED",
                        processed_time=stamp(),
                    )
                    result["line_results"] = [
                        {
                            "line_no": o["line_no"],
                            "status": "FAILED" if partial and i == 1 else "PROCESSED",
                            "his_order_no": None
                            if partial and i == 1
                            else f"DEMO_ORDER_{record_id}_{o['line_no']}",
                            "error_code": "DEMO_STOCK_EMPTY" if partial and i == 1 else None,
                            "error_message": "模拟缺货，用于演示部分失败"
                            if partial and i == 1
                            else None,
                            "retryable": False if partial and i == 1 else None,
                        }
                        for i, o in enumerate(submitted["orders"])
                    ]
                    db.execute(
                        "UPDATE records SET status=?,receipt=? WHERE id=?",
                        (result["handover_status"], json.dumps(result), record_id),
                    )
            elif name == "B_CancelRegimenHandover":
                if not row or row["archive"]:
                    raise BusinessError(
                        "CANCEL_NOT_ALLOWED", "尚未提交或已归档的演示记录不可撤销", 409
                    )
                receipt = json.loads(row["receipt"])
                result = {
                    "handover_status": "CANCELLED",
                    "processed_time": stamp(),
                    "hospital_business_ref": receipt.get("hospital_business_ref"),
                    "line_results": [
                        {"line_no": o["line_no"], "status": "CANCELLED"}
                        for o in json.loads(row["payload"])["orders"]
                    ],
                }
                db.execute(
                    "UPDATE records SET status=?,receipt=? WHERE id=?",
                    ("CANCELLED", json.dumps(result), record_id),
                )
            elif name == "B_ArchiveRegimenRecord":
                if not row or row["status"] != "PROCESSED":
                    raise BusinessError(
                        "HANDOVER_NOT_COMPLETE", "全部模拟医嘱处理成功后才能归档", 409
                    )
                submitted = json.loads(row["payload"])
                if fingerprint(body["orders"]) != fingerprint(submitted["orders"]):
                    raise BusinessError("ARCHIVE_CONTENT_CHANGED", "归档医嘱与提交内容不一致", 409)
                result = {
                    "document_id": "DEMO_EMR_" + record_id,
                    "archive_status": "ARCHIVED",
                    "processed_time": stamp(),
                    "signature_status": "UNSIGNED",
                    "message": "模拟病历归档完成，电子签名需另行回查",
                }
                db.execute(
                    "UPDATE records SET archive=? WHERE id=?", (json.dumps(result), record_id)
                )
            else:
                if not row or not row["archive"]:
                    raise BusinessError("NOT_ARCHIVED", "尚未归档该演示修订", 409)
                result = json.loads(row["archive"])
                result.pop("message", None)
                result.update(
                    patient_regimen_record_id=record_id,
                    signature_status="SIGNED",
                    signature_id="DEMO_SIGN_" + record_id,
                    signature_time=stamp(),
                    processed_time=stamp(),
                )
                result.update(DOCTORS)
                db.execute(
                    "UPDATE records SET archive=? WHERE id=?", (json.dumps(result), record_id)
                )
            db.execute(
                "INSERT INTO calls(operation,request_hash,response,time) VALUES(?,?,?,?)",
                (name, fingerprint(body), json.dumps(result), stamp()),
            )
            if command:
                db.execute(
                    "INSERT INTO commands VALUES(?,?,?)",
                    (command, fingerprint(body), json.dumps(result)),
                )
            return result

    def summary(self, record_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            return {
                "handover_status": "NOT_SUBMITTED",
                "archive_status": "NOT_ARCHIVED",
                "signature_status": "UNSIGNED",
            }
        payload, receipt = json.loads(row["payload"]), json.loads(row["receipt"])
        archive = json.loads(row["archive"]) if row["archive"] else {}
        return {
            **receipt,
            **archive,
            "verification": reconcile_receipt(
                HandoverResponse.model_validate(receipt), {o["line_no"] for o in payload["orders"]}
            ),
        }
