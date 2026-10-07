"""Real PostgreSQL contract verification, fully rolled back in an isolated database."""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from chemo_agent_product.config import Settings
from chemo_agent_product.database import initialize, insert
from chemo_agent_product.domain import Fact, PatientSnapshot, Reference, fingerprint
from chemo_agent_product.hospital import SourcePayload
from chemo_agent_product.patient_contracts import ConfirmInput, LaunchInput, SaveInput
from chemo_agent_product.security import BusinessError, Principal
from chemo_agent_product.worker import Worker
from chemo_agent_product.workflow import Workflow


class TransactionPool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest_asyncio.fixture
async def environment():
    dsn = os.environ.get("CHEMO_WORKFLOW_TEST_DSN")
    if not dsn:
        pytest.skip("explicit isolated test DSN required")
    if "test" not in dsn.rsplit("/", 1)[-1].lower():
        raise RuntimeError("refusing non-test database")
    c = await asyncpg.connect(dsn)
    await initialize(c)
    tx = c.transaction()
    await tx.start()
    try:
        h = await insert(
            c,
            "integration.hospital",
            dict(
                created_by_principal="contract-test",
                hospital_key=f"TEST_{uuid4()}",
                name="临时合同测试医院",
                contract_version="v1.0.1",
                adapter_profile_ref="CONTRACT_ONLY",
                status="TEST_ONLY",
            ),
        )
        s = await insert(
            c,
            "clinical.staff_reference",
            dict(
                created_by_principal="contract-test",
                hospital_id=h["id"],
                external_staff_id="CONTRACT_DOCTOR",
            ),
        )
        p = Principal(
            subject="contract-test",
            hospital_id=h["id"],
            staff_id=s["id"],
            roles=["DOCTOR"],
            expires_at=4102444800,
        )
        pool = TransactionPool(c)
        settings = Settings(environment="test", worker_enabled=False)
        yield c, Workflow(pool, settings), p, pool, settings
    finally:
        await tx.rollback()
        await c.close()


def launch_input(patient="CONTRACT_PATIENT", encounter="CONTRACT_ENCOUNTER", previous=None):
    return LaunchInput(
        patient_id=patient,
        encounter_id=encounter,
        operator_id="CONTRACT_DOCTOR",
        operator_name="合同测试医生",
        dept_code="CONTRACT_ONLY",
        operator_dept_name="合同测试",
        session_scope_ref="CONTRACT_SESSION",
        previous_context_id=previous,
    )


class ReaderDouble:
    async def fetch(self, patient, encounter, operator):
        payload = {"patient_id": patient, "encounter_id": encounter, "disease": "乳腺肿瘤"}
        return [
            SourcePayload(
                operation="Q_GetPatientClinicalData",
                source_key=f"{encounter}:contract",
                received_at=datetime.now(UTC),
                identity_state="CONTRACT_TEST_ONLY",
                payload=payload,
                content_hash=fingerprint(payload),
            )
        ]

    def normalize(self, sources, patient, encounter):
        return PatientSnapshot(
            patient_ref=patient,
            encounter_ref=encounter,
            captured_at=datetime.now(UTC),
            facts={
                "disease": Fact(
                    code="disease",
                    value="乳腺肿瘤",
                    status="CONFIRMED",
                    source=Reference(
                        namespace="contract-test",
                        id="source",
                        version="1",
                        content_hash=sources[0].content_hash,
                    ),
                )
            },
        )


@pytest.mark.asyncio
async def test_patient_flow_idempotency_save_confirm_and_history(environment):
    c, w, p, pool, settings = environment
    first = await w.launch(p, launch_input(), "launch-1")
    assert first == await w.launch(p, launch_input(), "launch-1")
    with pytest.raises(BusinessError, match="IDEMPOTENCY_CONFLICT"):
        await w.launch(p, launch_input("OTHER", "OTHER"), "launch-1")
    context_id = UUID(first["context_id"])
    worker = Worker(pool, settings, ReaderDouble())
    job = await worker.claim()
    assert job["status"] == "RUNNING"
    await worker.prepare(job)
    read = await w.read_context(p, context_id)
    assert read["prepare_status"] == "SUCCEEDED" and read["candidates"]
    candidate = read["candidates"][0]["candidate_id"]
    selected = await w.select(p, context_id, candidate, "select-1")
    instance = UUID(selected["instance_id"])
    assert selected == await w.select(p, context_id, candidate, "select-1")
    editor = await w.read_instance(p, context_id, instance)
    assert editor["revision_id"] is None
    saved = await w.save(
        p,
        context_id,
        instance,
        SaveInput(expected_row_version=1, field_values={"current_cycle": 1}),
        "save-1",
    )
    restored = await w.read_instance(p, context_id, instance)
    assert restored["field_values"]["current_cycle"] == 1
    assert restored["revision_hash"] == saved["revision_hash"]
    with pytest.raises(BusinessError, match="REVISION_CONFLICT"):
        await w.save(p, context_id, instance, SaveInput(expected_row_version=1), "save-stale")
    confirmed = await w.confirm(
        p,
        context_id,
        instance,
        ConfirmInput(
            revision_id=UUID(saved["revision_id"]),
            revision_hash=saved["revision_hash"],
            expected_row_version=2,
            acknowledged_codes=[i["code"] for i in saved["issues"]],
        ),
        "confirm-1",
    )
    assert confirmed["mode"] == "TEST_ONLY" and confirmed["hospital_status"] == "NOT_SUBMITTED"
    saved2 = await w.save(
        p,
        context_id,
        instance,
        SaveInput(
            expected_row_version=3,
            base_revision_id=UUID(saved["revision_id"]),
            field_values={"current_cycle": 2},
            change_reason="合同测试修订",
        ),
        "save-2",
    )
    assert saved2["revision_no"] == 2
    assert (
        await c.fetchval(
            "SELECT count(*) FROM clinical.patient_regimen_revision WHERE instance_id=$1", instance
        )
        == 2
    )
    assert (await w.read_instance(p, context_id, instance))["confirmed_revision_id"] is None
    from chemo_agent_product.agents import AgentService
    from chemo_agent_product.patient_contracts import AgentRequest

    service = AgentService(w)
    agent = await service.enqueue(
        p, context_id, AgentRequest(kind="RECOMMENDATION"), "main-not-configured"
    )
    assert agent["status"] == "FAILED" and agent["error_code"] == "MODEL_NOT_CONFIGURED"
    review = await service.enqueue(
        p,
        context_id,
        AgentRequest(kind="REVIEWER", revision_id=UUID(saved2["revision_id"])),
        "review-not-configured",
    )
    assert review["status"] == "FAILED"
    read = await service.read(p, context_id, UUID(review["agent_run_id"]))
    assert read["outputs"] == [] and read["tools"] == []
    assert read["revision_id"] == saved2["revision_id"]


@pytest.mark.asyncio
async def test_patient_switch_old_job_and_cross_scope_rejected(environment):
    c, w, p, pool, settings = environment
    first = await w.launch(p, launch_input(), "first")
    a = UUID(first["context_id"])
    second = await w.launch(p, launch_input("OTHER_PATIENT", "OTHER_ENCOUNTER", a), "second")
    b = UUID(second["context_id"])
    worker = Worker(pool, settings, ReaderDouble())
    job = await worker.claim()
    await worker.prepare(job)
    assert await c.fetchval("SELECT status FROM ops.job WHERE id=$1", job["id"]) == "CANCELLED"
    with pytest.raises(BusinessError, match="CONTEXT_EXPIRED"):
        await w.refresh(p, a, "refresh", 1)
    with pytest.raises(BusinessError, match="CONTEXT_FORBIDDEN"):
        await w.read_context(p.model_copy(update={"staff_id": uuid4()}), b)


@pytest.mark.asyncio
async def test_missing_hospital_fails_without_fake_candidate(environment):
    c, w, p, pool, settings = environment
    result = await w.launch(p, launch_input(), "launch")
    worker = Worker(pool, settings)
    assert await worker.once()
    state = await w.read_context(p, UUID(result["context_id"]))
    assert state["prepare_status"] == "FAILED"
    assert state["prepare_error_code"] == "HOSPITAL_NOT_CONFIGURED"
    assert state["candidates"] == [] and state["snapshot"] is None


@pytest.mark.asyncio
async def test_expired_lease_is_recovered_and_old_owner_cannot_finish(environment):
    c, w, p, pool, settings = environment
    await w.launch(p, launch_input(), "launch")
    worker1 = Worker(pool, settings, ReaderDouble())
    first = await worker1.claim()
    await c.execute(
        "UPDATE ops.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
        first["id"],
    )
    worker2 = Worker(pool, settings, ReaderDouble())
    second = await worker2.claim()
    assert second["lease_epoch"] == first["lease_epoch"] + 1
    with pytest.raises(BusinessError, match="JOB_LEASE_LOST"):
        async with c.transaction():
            await worker1.fenced(c, first)
    await worker2.prepare(second)
    assert await c.fetchval("SELECT status FROM ops.job WHERE id=$1", second["id"]) == "SUCCEEDED"
