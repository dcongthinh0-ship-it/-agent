"""Loopback-only UI contract harness. ALL fixture writes are rolled back on shutdown.

Run with the frontend proxy pointing at this API. This file is not part of the
production app and never generates a persistent demonstration patient dataset.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import uvicorn
from fastapi import Request
from fastapi.responses import HTMLResponse
from pydantic import SecretStr

from chemo_agent_product.agents import AgentService
from chemo_agent_product.api import create_app
from chemo_agent_product.catalog import PostgresCatalogReader
from chemo_agent_product.config import Settings
from chemo_agent_product.database import initialize, insert
from chemo_agent_product.domain import Fact, PatientSnapshot, Reference, fingerprint
from chemo_agent_product.hospital import SourcePayload
from chemo_agent_product.security import BusinessError, Principal, issue_test_token
from chemo_agent_product.worker import Worker
from chemo_agent_product.workflow import Workflow


class RollbackPool:
    def __init__(self, connection):
        self.connection = connection
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self):
        async with self.lock:
            yield self.connection


class ContractReader:
    async def fetch(self, patient, encounter, operator):
        value = {"patient_id": patient, "encounter_id": encounter, "kind": "CONTRACT_TEST_ONLY"}
        return [
            SourcePayload(
                operation="Q_GetPatientClinicalData",
                source_key=f"{encounter}:contract",
                received_at=datetime.now(UTC),
                identity_state="CONTRACT_TEST_ONLY",
                payload=value,
                content_hash=fingerprint(value),
            )
        ]

    def normalize(self, sources, patient, encounter):
        ref = Reference(
            namespace="contract-test",
            id=sources[0].source_key,
            version="1",
            content_hash=sources[0].content_hash,
        )
        now = datetime.now(UTC)
        facts = {
            code: Fact(
                code=code, value=value, unit=unit, status="CONFIRMED", observed_at=now, source=ref
            )
            for code, value, unit in [
                ("disease", "乳腺肿瘤", None),
                ("patient_name", f"合同验收 {patient[-1]}", None),
                ("height", 170, "cm"),
                ("weight", 60, "kg"),
            ]
        }
        return PatientSnapshot(
            patient_ref=patient, encounter_ref=encounter, captured_at=now, facts=facts
        )


HOST_HTML = (Path(__file__).parent / "fixtures" / "acceptance-host.html").read_text()


async def serve(database, port, frontend):
    configured = Settings(_env_file=".env")
    if not configured.database_url or "test" not in database.lower():
        raise RuntimeError("an explicit test database and configured catalog source are required")
    origin = urlsplit(configured.database_url.get_secret_value())
    source = await asyncpg.connect(configured.database_url.get_secret_value())
    await initialize(source)
    try:
        assets = await source.fetch("""SELECT a.* FROM catalog_bridge.form_layout_asset a
            JOIN regimen_catalog.regimen_version v ON v.id=a.regimen_version_id
            JOIN regimen_catalog.regimen r ON r.id=v.regimen_id
            WHERE r.regimen_code IN ('WFAH-BC-001','WFAH-BC-004')""")
    finally:
        await source.close()
    c = await asyncpg.connect(urlunsplit(origin._replace(path=f"/{database}")))
    await initialize(c)
    tx = c.transaction()
    await tx.start()
    try:
        for asset in assets:
            present = await c.fetchval(
                "SELECT id FROM catalog_bridge.form_layout_asset WHERE id=$1", asset["id"]
            )
            if not present:
                await insert(c, "catalog_bridge.form_layout_asset", dict(asset))
        hospital = await insert(
            c,
            "integration.hospital",
            dict(
                created_by_principal="ui-contract-test",
                hospital_key=f"TEST_UI_{secrets.token_hex(8)}",
                name="临时合同验收",
                contract_version="v1.0.1",
                adapter_profile_ref="CONTRACT_TEST_ONLY",
                status="TEST_ONLY",
            ),
        )
        staff = await insert(
            c,
            "clinical.staff_reference",
            dict(
                created_by_principal="ui-contract-test",
                hospital_id=hospital["id"],
                external_staff_id="CONTRACT_DOCTOR",
            ),
        )
        key = secrets.token_urlsafe(48)
        config = Settings(
            environment="test",
            database_url=None,
            runtime_test_database_url=None,
            worker_enabled=False,
            model_enabled=False,
            model_api_key=None,
            launch_signing_key=SecretStr(key),
            trusted_host_origins=[f"http://127.0.0.1:{port}"],
            hospital_adapter_config=None,
            hospital_delivery_config=None,
        )
        actor = Principal(
            subject="ui-contract-test",
            hospital_id=hospital["id"],
            staff_id=staff["id"],
            roles=["DOCTOR", "OPERATOR", "KNOWLEDGE_REVIEWER"],
            expires_at=4102444800,
        )
        pool = RollbackPool(c)
        workflow = Workflow(pool, config)
        worker = Worker(pool, config, ContractReader())
        agents = AgentService(workflow)
        worker.agent_handler = agents.run_job
        app = create_app(config, reader=PostgresCatalogReader(pool))
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            async with original_lifespan(app):
                app.state.workflow, app.state.agents = workflow, agents
                app.state.database_status, app.state.context_status = "CONNECTED", "TEST_ONLY"
                task = asyncio.create_task(worker.loop())
                try:
                    yield
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await tx.rollback()
                    remaining = await c.fetchval(
                        "SELECT count(*) FROM integration.hospital "
                        "WHERE hospital_key LIKE 'TEST_UI_%'"
                    )
                    print(
                        {"rollback_complete": True, "remaining_harness_hospitals": remaining},
                        flush=True,
                    )
                    await c.close()

        app.router.lifespan_context = lifespan

        @app.get("/acceptance/host", response_class=HTMLResponse)
        async def host():
            return HOST_HTML.replace("__FRONTEND__", frontend)

        @app.post("/acceptance/token")
        async def token(request: Request):
            value = await request.json()
            if (
                value.get("patient_id") not in {"CONTRACT_PATIENT_A", "CONTRACT_PATIENT_B"}
                or value.get("operator_id") != "CONTRACT_DOCTOR"
            ):
                raise BusinessError("CONTRACT_SCOPE_ONLY", "仅限隔离验收患者", 403)
            return {"access_token": issue_test_token(actor, key)}

        @app.get("/acceptance/summary")
        async def summary():
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT lc.host_generation,lc.context_state,pr.status,pr.stage
                    FROM clinical.launch_context lc
                    LEFT JOIN clinical.prepare_run pr ON pr.id=lc.current_prepare_run_id
                    WHERE lc.hospital_id=$1 ORDER BY lc.host_generation""",
                    hospital["id"],
                )
            return {
                "test_only": True,
                "rollback_on_shutdown": True,
                "contexts": [dict(row) for row in rows],
            }

        print(
            {
                "url": f"http://127.0.0.1:{port}/acceptance/host",
                "database": database,
                "test_only": True,
                "rollback_on_shutdown": True,
            }
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        )
        await server.serve()
    finally:
        if not c.is_closed():
            await c.close()  # PostgreSQL rolls back any transaction on disconnect.


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--frontend", default="http://127.0.0.1:5175")
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.database, args.port, args.frontend))
    except KeyboardInterrupt:
        pass
