"""Explicit demo entry point; the production application has no mock-hospital routes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import asyncpg
import uvicorn
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import SecretStr

from chemo_agent_product.api import create_app
from chemo_agent_product.config import Settings
from chemo_agent_product.database import insert
from chemo_agent_product.demo_fixtures import (
    DOCTORS,
    PATIENTS,
    encounter_matches,
    hospital_read,
    read_profile,
    stamp,
)
from chemo_agent_product.demo_hospital import DemoHospital
from chemo_agent_product.domain import FactRequirement, fingerprint
from chemo_agent_product.hospital import READ_OPERATIONS
from chemo_agent_product.hospital_contracts import REQUEST_CONTRACTS
from chemo_agent_product.hospital_delivery import ConfiguredHospitalDelivery
from chemo_agent_product.knowledge import load_inputs
from chemo_agent_product.patient_api import principal
from chemo_agent_product.security import BusinessError, Principal, issue_test_token
from chemo_agent_product.worker import Worker

PrincipalDep = Annotated[Principal, Depends(principal)]

DATABASE = "chemo_demo_test_20261008"
DOSES = {"帕妥珠单抗": "840 mg", "曲妥珠单抗": "480 mg", "多西他赛": "120 mg", "卡铂": "500 mg"}


def fresh_demo_token(actor, signing_key):
    return issue_test_token(
        actor.model_copy(update={"expires_at": int(time.time()) + 86400}), signing_key
    )


async def demo_knowledge(c, disease, usage_mode):
    plans, manifest = await load_inputs(c, disease, usage_mode)
    for plan in plans:
        plan.requirements = [
            FactRequirement(fact_code=code, unit=unit, max_age_days=days)
            for code, unit, days in [
                ("height", "cm", 30),
                ("weight", "kg", 30),
                ("wbc", "10^9/L", 3),
                ("anc", "10^9/L", 3),
                ("platelets", "10^9/L", 3),
                ("creatinine", "umol/L", 7),
            ]
        ]
    manifest["demo_configuration"] = "DEMO_INPUTS_ONLY_NOT_CLINICAL_APPROVAL"
    manifest["calculation"] = {
        "bsa_formula_approved": True,
        "measurement_max_age_days": 30,
        "usage_mode": "DEMO_ONLY",
        "source": "fictional demo fixture",
    }
    return plans, manifest


async def setup(source_env: Path, state: Path, origin: str):
    state.mkdir(parents=True, exist_ok=True)
    source = Settings(_env_file=source_env)
    if not source.database_url:
        raise RuntimeError("source database must be configured")
    parsed = urlsplit(source.database_url.get_secret_value())
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("demo setup accepts only the local PostgreSQL source")
    c = await asyncpg.connect(source.database_url.get_secret_value())
    try:
        if not await c.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", DATABASE):
            await c.execute("CREATE DATABASE " + DATABASE)
            dumped = subprocess.run(
                [
                    "docker",
                    "exec",
                    "chemo-regimen-catalog-db",
                    "pg_dump",
                    "-U",
                    parsed.username,
                    "-Fc",
                    "-d",
                    parsed.path[1:],
                ],
                capture_output=True,
                check=True,
            ).stdout
            restored = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "chemo-regimen-catalog-db",
                    "pg_restore",
                    "-U",
                    parsed.username,
                    "--no-owner",
                    "--no-acl",
                    "-d",
                    DATABASE,
                ],
                input=dumped,
                capture_output=True,
            )
            if restored.returncode:
                raise RuntimeError("demo database restore failed; inspect the local PostgreSQL log")
    finally:
        await c.close()
    config_path = state / "runtime.json"
    runtime = (
        json.loads(config_path.read_text())
        if config_path.exists()
        else {
            "launch_signing_key": secrets.token_urlsafe(48),
            "hospital_auth": secrets.token_urlsafe(48),
        }
    )
    runtime["database_url"] = urlunsplit(parsed._replace(path="/" + DATABASE))
    runtime["api_origin"] = origin
    config_path.write_text(json.dumps(runtime))
    config_path.chmod(0o600)
    (state / "hospital-read.json").write_text(
        json.dumps(read_profile(origin), ensure_ascii=False, indent=2)
    )
    (state / "hospital-delivery.json").write_text(
        json.dumps(
            {
                "contract_version": "v1.0.1",
                "hospital_code": "H1001",
                "credential_environment_variable": "CHEMO_DEMO_HOSPITAL_AUTH",
                "operations": {
                    name: {"url": f"{origin}/workstation/hospital/{name}"}
                    for name in REQUEST_CONTRACTS
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    (state / "patients.json").write_text(json.dumps(PATIENTS, ensure_ascii=False, indent=2))
    return source, runtime


def create_demo_app(source, runtime, state, frontend):
    if urlsplit(runtime["database_url"]).path != "/" + DATABASE or urlsplit(
        runtime["database_url"]
    ).hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("refusing to run demo writes against a different database")
    origin = runtime["api_origin"]
    if urlsplit(origin).hostname != "127.0.0.1":
        raise RuntimeError("demo hospital must be loopback-only")
    if urlsplit(frontend).hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("demo frontend must be loopback-only")
    os.environ["CHEMO_DEMO_HOSPITAL_AUTH"] = runtime["hospital_auth"]
    config = source.model_copy(
        update={
            "environment": "test",
            "database_url": SecretStr(runtime["database_url"]),
            "runtime_test_database_url": None,
            "worker_enabled": False,
            "launch_signing_key": SecretStr(runtime["launch_signing_key"]),
            "trusted_host_origins": [origin],
            "context_ttl_seconds": 86400,
            "hospital_adapter_config": str(state / "hospital-read.json"),
            "hospital_delivery_config": str(state / "hospital-delivery.json"),
        }
    )
    app = create_app(config)
    app.state.demo_mode = True
    hospital = DemoHospital(state / "hospital.sqlite3")
    delivery = ConfiguredHospitalDelivery(config.hospital_delivery_config)
    original = app.router.lifespan_context
    actor = None
    dictionary = []

    @asynccontextmanager
    async def lifespan(app):
        nonlocal actor, dictionary
        async with original(app):
            w = app.state.workflow
            if not w:
                raise RuntimeError("demo database has no runnable patient workflow")
            async with w.pool.acquire() as c, c.transaction():
                h = await c.fetchrow(
                    "SELECT * FROM integration.hospital WHERE hospital_key='DEMO_20261008'"
                )
                if not h:
                    h = await insert(
                        c,
                        "integration.hospital",
                        dict(
                            created_by_principal="demo-seed",
                            hospital_key="DEMO_20261008",
                            name="肿瘤诊疗工作站",
                            contract_version="v1.0.1",
                            adapter_profile_ref="DEMO_HTTP_V1",
                            status="TEST_ONLY",
                        ),
                    )
                staff = await c.fetchrow(
                    "SELECT * FROM clinical.staff_reference "
                    "WHERE hospital_id=$1 AND external_staff_id=$2",
                    h["id"],
                    "DR1001",
                )
                if not staff:
                    staff = await insert(
                        c,
                        "clinical.staff_reference",
                        dict(
                            created_by_principal="demo-seed",
                            hospital_id=h["id"],
                            external_staff_id="DR1001",
                        ),
                    )
                actor = Principal(
                    subject="demo-workstation",
                    hospital_id=h["id"],
                    staff_id=staff["id"],
                    roles=["DOCTOR", "KNOWLEDGE_REVIEWER", "OPERATOR"],
                    expires_at=int(time.time()) + 86400,
                )
                dictionary = [
                    {
                        "dictionary_type": "DRUG",
                        "item_code": "DRG_" + fingerprint(m["name"])[:12],
                        "item_name": m["name"],
                        "common_name": m["name"],
                        "dose_unit": "mg",
                        "order_unit": "瓶",
                        "status": "ENABLED",
                        "route_code": "IV_INFUSION",
                        "frequency_code": "ONC01E",
                        "execution_dept_code": "ONC01",
                        "specification": "注射剂",
                    }
                    for m in await c.fetch(
                        "SELECT DISTINCT source_drug_name AS name "
                        "FROM regimen_catalog.regimen_medication_item"
                    )
                ]
            worker = Worker(w.pool, config, knowledge_loader=demo_knowledge)
            worker.agent_handler = app.state.agents.run_job
            task = asyncio.create_task(worker.loop())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan

    @app.get("/workstation/runtime", include_in_schema=False)
    async def runtime_identity():
        return {"workspace": str(Path(__file__).resolve().parents[3]), "database": DATABASE}

    @app.get("/workstation", response_class=HTMLResponse)
    @app.get("/demo/host", response_class=HTMLResponse, include_in_schema=False)
    async def host():
        return (
            Path(__file__)
            .with_name("demo-host.html")
            .read_text()
            .replace("__FRONTEND__", frontend)
            .replace("__PATIENTS__", json.dumps(PATIENTS, ensure_ascii=False))
        )

    @app.post("/workstation/token")
    @app.post("/demo/token", include_in_schema=False)
    async def token(request: Request):
        body = await request.json()
        if body.get("patient_id") not in PATIENTS or body.get("operator_id") != "DR1001":
            raise BusinessError("WORKSTATION_SCOPE_ONLY", "当前患者或医师不在授权范围内", 403)
        return {"access_token": fresh_demo_token(actor, runtime["launch_signing_key"])}

    @app.post("/workstation/hospital/{name}")
    @app.post("/demo/hospital/{name}", include_in_schema=False)
    async def simulated_hospital(name: str, request: Request):
        if not secrets.compare_digest(
            request.headers.get("Authorization", ""), runtime["hospital_auth"]
        ):
            raise BusinessError("WORKSTATION_AUTH_REQUIRED", "院方接口认证未通过", 401)
        body = await request.json()
        if name in READ_OPERATIONS:
            pid, eid = body.get("patient_id"), body.get("encounter_id", "")
            if not encounter_matches(pid, eid):
                raise BusinessError("WORKSTATION_PATIENT_MISMATCH", "患者与就诊不一致", 403)
            return hospital_read(name, body, dictionary)
        if name not in REQUEST_CONTRACTS:
            raise BusinessError("UNKNOWN_OPERATION", "未定义的院方接口操作", 404)
        return {
            "code": "0",
            "msg": "接口处理完成",
            "demo": True,
            "data": {"content": hospital.invoke(name, body)},
        }

    async def session(ctx, iid, p):
        if p.hospital_id != actor.hospital_id:
            raise BusinessError("WORKSTATION_SCOPE_ONLY", "请使用当前工作站的患者上下文", 403)
        w = app.state.workflow
        value = await w.read_instance(p, ctx, iid)
        if value["read_only"]:
            raise BusinessError("WORKSTATION_CONTEXT_STALE", "请使用本次患者快照下的方案", 409)
        return value

    @app.get("/workstation/contexts/{ctx}/instances/{iid}/example")
    @app.get("/demo/contexts/{ctx}/instances/{iid}/example", include_in_schema=False)
    async def example(ctx: UUID, iid: UUID, p: PrincipalDep):
        value = await session(ctx, iid, p)
        if value["template"]["regimen_code"] != "WFAH-BC-001":
            raise BusinessError(
                "PRESET_NOT_DEFINED", "BC-001已配置预设填写值，请自行填写其他方案", 422
            )
        return {
            "demo": True,
            "field_values": {
                "current_cycle": 1,
                "total_cycles": 6,
                "treatment_date": datetime.now().date().isoformat(),
                "treatment_time": "09:00",
            },
            "medication_values": {
                m["item_key"]: {
                    "actual_dose_text": DOSES[m["source_drug_name"]],
                    "administration_day_text": "第1天",
                    "instructions": "给药前核对剂量、治疗日期与给药安排。",
                }
                for m in value["template"]["medications"]
            },
        }

    async def payload(ctx, iid, p):
        value = await session(ctx, iid, p)
        if not value["revision_id"] or value["revision_id"] != value["confirmed_revision_id"]:
            raise BusinessError("CONFIRMATION_REQUIRED", "先保存并单独确认当前修订", 409)
        if value["template"]["regimen_code"] != "WFAH-BC-001":
            raise BusinessError(
                "ORDER_MAPPING_NOT_DEFINED",
                "当前已配置BC-001的四条主治疗药，其他医嘱映射待配置",
                422,
            )
        fields, orders = value["field_values"], []
        for i, med in enumerate(value["template"]["medications"], 1):
            edit = value["medication_values"].get(med["item_key"], {})
            dose = re.fullmatch(
                r"\s*(\d+(?:\.\d+)?)\s*mg\s*", edit.get("actual_dose_text", ""), re.I
            )
            if (
                not dose
                or float(dose[1]) <= 0
                or edit.get("administration_day_text") not in {"第1天", "D1", "1"}
            ):
                raise BusinessError(
                    "ORDER_INCOMPLETE",
                    "当前医嘱映射要求正数mg剂量及第1天，复杂日程需另行配置",
                    422,
                )
            orders.append(
                {
                    "line_no": i,
                    "order_category": "MAIN_TREATMENT",
                    "item_type": "DRUG",
                    "drug_code": "DRG_" + fingerprint(med["source_drug_name"])[:12],
                    "drug_name": med["source_drug_name"],
                    "dose_value": dose[1],
                    "dose_unit": "mg",
                    "quantity": 1,
                    "quantity_unit": "瓶",
                    "route_code": "IV_INFUSION",
                    "frequency_code": "ONC01E",
                    "start_day": "1",
                    "long_term_flag": "N",
                    "execution_dept_code": "ONC01",
                    "remark": "本批次包含主要治疗医嘱。",
                    "special_instructions": edit.get("instructions") or None,
                }
            )
        async with app.state.workflow.pool.acquire() as c:
            confirmed = await c.fetchval(
                "SELECT max(acted_at) FROM clinical.doctor_action_event "
                "WHERE instance_id=$1 AND revision_id=$2 AND action_type='CONFIRM'",
                iid,
                UUID(value["revision_id"]),
            )
        if not confirmed:
            raise BusinessError("CONFIRMATION_MISSING", "确认记录缺失，请重新核对该修订", 409)
        if not fields.get("treatment_date"):
            raise BusinessError("TREATMENT_DATE_REQUIRED", "请填写治疗日期后另存并确认修订", 422)
        # Stable times and keys ensure repeated clicks reuse the original hospital batch.
        base = {
            "patient_regimen_record_id": hospital.record_id(value["revision_id"]),
            "patient_id": value["snapshot"]["patient_ref"],
            "encounter_id": value["snapshot"]["encounter_ref"],
            "confirmed_regimen": {
                "regimen_name": value["template"]["display_name"],
                "decision_status": "CONFIRMED",
                "confirmed_by": "DR1001",
                "confirmed_time": stamp(confirmed),
                "regimen_code": "WFAH-BC-001",
                "regimen_cycle_no": fields.get("current_cycle"),
                "regimen_total_cycles": fields.get("total_cycles"),
            },
            "orders": orders,
        }
        return value, base

    @app.get("/workstation/contexts/{ctx}/instances/{iid}/delivery")
    @app.get("/demo/contexts/{ctx}/instances/{iid}/delivery", include_in_schema=False)
    async def delivery_state(ctx: UUID, iid: UUID, p: PrincipalDep):
        value = await session(ctx, iid, p)
        return {
            "demo": True,
            "scope": "BC-001四条主治疗药",
            **hospital.summary(hospital.record_id(value["revision_id"])),
        }

    @app.post("/workstation/contexts/{ctx}/instances/{iid}/delivery/{action}")
    @app.post("/demo/contexts/{ctx}/instances/{iid}/delivery/{action}", include_in_schema=False)
    async def deliver(ctx: UUID, iid: UUID, action: str, p: PrincipalDep):
        value, base = await payload(ctx, iid, p)
        rid = base["patient_regimen_record_id"]
        names = {
            "validate": "B_ValidateChemoOrders",
            "import": "B_ImportChemoOrders",
            "query": "Q_GetRegimenHandoverStatus",
            "cancel": "B_CancelRegimenHandover",
            "archive": "B_ArchiveRegimenRecord",
            "signature": "Q_GetRegimenArchiveStatus",
        }
        if action not in names:
            raise BusinessError("UNKNOWN_ACTION", "未定义的交付操作", 404)
        if action == "validate":
            body = {**base, "doctor_id": "DR1001", "dept_code": "ONC01"}
        elif action == "import":
            body = {
                **base,
                "doctor_id": "DR1001",
                "dept_code": "ONC01",
                "idempotency_key": rid + ":import",
                "visit_type": "INPATIENT",
                "regimen_version": str(value["template"]["version_id"]),
                "data_version": value["revision_hash"],
                "planned_start_time": value["field_values"]["treatment_date"].replace("-", "")
                + "090000000",
            }
        elif action in {"query", "signature"}:
            body = {"patient_regimen_record_id": rid}
        elif action == "cancel":
            body = {
                "patient_regimen_record_id": rid,
                "idempotency_key": rid + ":cancel",
                "cancel_reason": "医生撤销本批次医嘱",
            }
        else:
            body = {
                **base,
                "idempotency_key": rid + ":archive",
                "document_type": "CHEMO_REGIMEN",
                **DOCTORS,
            }

        async def observer(event):
            if event["phase"] == "COMPLETED":
                async with app.state.workflow.pool.acquire() as c, c.transaction():
                    context = await app.state.workflow.scoped_context(c, ctx, p)
                    await app.state.workflow.audit(
                        c,
                        p,
                        context,
                        "DEMO_HOSPITAL_CALL",
                        iid,
                        {
                            k: str(event.get(k))
                            for k in [
                                "operation_name",
                                "request_id",
                                "request_hash",
                                "response_hash",
                                "transport_outcome",
                                "http_status",
                            ]
                        },
                    )

        result, _ = await delivery.invoke(names[action], body, "DR1001", observer=observer)
        return {
            "demo": True,
            "operation": names[action],
            "result": result.model_dump(mode="json", exclude_none=True),
            "state": hospital.summary(rid),
        }

    return app


async def run(args):
    state = Path(args.state).resolve()
    origin = f"http://127.0.0.1:{args.port}"
    source, runtime = await setup(Path(args.source_env), state, origin)
    app = create_demo_app(source, runtime, state, args.frontend)
    print(
        {
            "workstation_url": origin + "/workstation",
            "database": DATABASE,
            "synthetic_patients": len(PATIENTS),
        },
        flush=True,
    )
    await uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    ).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", default=".env")
    parser.add_argument("--state", default="../artifacts/demo-20261008")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--frontend", default="http://127.0.0.1:5174")
    asyncio.run(run(parser.parse_args()))
