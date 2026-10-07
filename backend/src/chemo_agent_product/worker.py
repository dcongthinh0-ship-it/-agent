"""Database-backed worker with leases, recovery and generation fencing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import httpx

from chemo_agent_product.catalog import PostgresCatalogReader
from chemo_agent_product.config import Settings
from chemo_agent_product.database import insert
from chemo_agent_product.domain import Finding, fingerprint
from chemo_agent_product.hospital import ConfiguredHospitalReader, HospitalReader
from chemo_agent_product.knowledge import fixed_projection, load_inputs
from chemo_agent_product.matching import assess
from chemo_agent_product.security import BusinessError


class Worker:
    def __init__(
        self, pool: asyncpg.Pool, settings: Settings, reader: HospitalReader | None = None
    ):
        self.pool, self.settings = pool, settings
        self.reader = reader or ConfiguredHospitalReader(settings.hospital_adapter_config)
        self.owner = f"patient-worker:{uuid4()}"
        self.agent_handler = None

    async def claim(self):
        async with self.pool.acquire() as c, c.transaction():
            dead = await c.fetch("""UPDATE ops.job SET status='DEAD_LETTER',last_error_code='LEASE_RECOVERY_EXHAUSTED'
              WHERE status='RUNNING' AND lease_expires_at<now() AND attempt_count>=max_attempts RETURNING prepare_run_id,agent_run_id""")
            for row in dead:
                if row["prepare_run_id"]:
                    await c.execute(
                        """UPDATE clinical.prepare_run SET status='FAILED',error_code='LEASE_RECOVERY_EXHAUSTED',completed_at=now()
                       WHERE id=$1 AND status IN ('QUEUED','RUNNING')""",
                        row["prepare_run_id"],
                    )
                if row["agent_run_id"]:
                    await c.execute(
                        "UPDATE agent.agent_run SET status='FAILED',error_code='LEASE_RECOVERY_EXHAUSTED',completed_at=now() WHERE id=$1 AND status IN ('QUEUED','RUNNING')",
                        row["agent_run_id"],
                    )
                    await c.execute(
                        "UPDATE agent.agent_tool_call SET status='FAILED',error_code='LEASE_RECOVERY_EXHAUSTED',completed_at=now() WHERE agent_run_id=$1 AND status='STARTED'",
                        row["agent_run_id"],
                    )
            job = await c.fetchrow("""SELECT * FROM ops.job WHERE job_kind IN ('PREPARE','AGENT_RUN')
              AND ((status IN ('QUEUED','RETRY_WAIT') AND available_at<=clock_timestamp())
               OR (status='RUNNING' AND lease_expires_at<clock_timestamp()))
              AND attempt_count<max_attempts ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1""")
            if not job:
                return None
            return await c.fetchrow(
                """UPDATE ops.job SET status='RUNNING',attempt_count=attempt_count+1,
              lease_owner=$2,lease_token=$3,lease_epoch=lease_epoch+1,lease_expires_at=$4,heartbeat_at=now()
              WHERE id=$1 RETURNING *""",
                job["id"],
                self.owner,
                uuid4(),
                datetime.now(UTC) + timedelta(seconds=self.settings.worker_lease_seconds),
            )

    async def fenced(self, c, job):
        current = await c.fetchrow("SELECT * FROM ops.job WHERE id=$1 FOR UPDATE", job["id"])
        if (
            not current
            or current["status"] != "RUNNING"
            or current["lease_token"] != job["lease_token"]
            or current["lease_epoch"] != job["lease_epoch"]
            or current["lease_expires_at"] <= datetime.now(UTC)
        ):
            raise BusinessError("JOB_LEASE_LOST", "任务租约已交给其他执行者", 409)

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(max(5, self.settings.worker_lease_seconds / 3))
            async with self.pool.acquire() as c:
                await c.execute(
                    """UPDATE ops.job SET heartbeat_at=now(),lease_expires_at=$4
                  WHERE id=$1 AND lease_token=$2 AND lease_epoch=$3 AND status='RUNNING'
                  AND lease_expires_at>now()""",
                    job["id"],
                    job["lease_token"],
                    job["lease_epoch"],
                    datetime.now(UTC) + timedelta(seconds=self.settings.worker_lease_seconds),
                )

    async def prepare(self, job):
        async with self.pool.acquire() as c, c.transaction():
            await self.fenced(c, job)
            run = await c.fetchrow(
                """SELECT pr.*,lc.hospital_id,lc.patient_reference_id,lc.encounter_reference_id,
              lc.context_state,lc.expires_at,lc.active_generation,pat.external_patient_id,enc.external_encounter_id,
              staff.external_staff_id FROM clinical.prepare_run pr
              JOIN clinical.launch_context lc ON lc.id=pr.launch_context_id
              JOIN clinical.patient_reference pat ON pat.id=lc.patient_reference_id
              JOIN clinical.encounter_reference enc ON enc.id=lc.encounter_reference_id
              JOIN clinical.staff_reference staff ON staff.id=lc.operator_staff_id WHERE pr.id=$1 FOR UPDATE OF pr,lc""",
                job["prepare_run_id"],
            )
            if (
                run["context_state"] != "ACTIVE"
                or run["generation"] != run["active_generation"]
                or run["expires_at"] <= datetime.now(UTC)
            ):
                await c.execute(
                    "UPDATE clinical.prepare_run SET status='SUPERSEDED',completed_at=now() WHERE id=$1",
                    run["id"],
                )
                await c.execute("UPDATE ops.job SET status='CANCELLED' WHERE id=$1", job["id"])
                return
            await c.execute(
                "UPDATE clinical.prepare_run SET status='RUNNING',stage='FETCHING',started_at=coalesce(started_at,now()),error_code=NULL WHERE id=$1",
                run["id"],
            )
        started = datetime.now(UTC)
        sources = await self.reader.fetch(
            run["external_patient_id"], run["external_encounter_id"], run["external_staff_id"]
        )
        snapshot = self.reader.normalize(
            sources, run["external_patient_id"], run["external_encounter_id"]
        )
        if (
            snapshot.patient_ref != run["external_patient_id"]
            or snapshot.encounter_ref != run["external_encounter_id"]
        ):
            raise BusinessError("SOURCE_PATIENT_MISMATCH", "数据标准化结果与当前患者不一致", 409)
        async with self.pool.acquire() as c, c.transaction():
            await self.fenced(c, job)
            context = await c.fetchrow(
                "SELECT * FROM clinical.launch_context WHERE id=$1 FOR UPDATE",
                run["launch_context_id"],
            )
            if (
                context["context_state"] != "ACTIVE"
                or context["active_generation"] != run["generation"]
                or context["expires_at"] <= datetime.now(UTC)
            ):
                await c.execute(
                    "UPDATE clinical.prepare_run SET status='SUPERSEDED',completed_at=now() WHERE id=$1",
                    run["id"],
                )
                await c.execute("UPDATE ops.job SET status='CANCELLED' WHERE id=$1", job["id"])
                return
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                f"snapshot:{run['encounter_reference_id']}",
            )
            source_ids = []
            for source in sources:
                source_row = await c.fetchrow(
                    """INSERT INTO clinical.source_record
                  (created_by_principal,hospital_id,patient_reference_id,source_operation,source_system,source_record_key,
                   received_at,identity_verification,record_payload,payload_schema_version,content_hash,source_encounter_external_id)
                  VALUES($1,$2,$3,$4,'HOSPITAL_ADAPTER',$5,$6,$7,$8,'hospital-response.v1',$9,$10)
                  ON CONFLICT(hospital_id,patient_reference_id,source_operation,source_record_key,content_hash)
                  DO NOTHING RETURNING id""",
                    run["created_by_principal"],
                    run["hospital_id"],
                    run["patient_reference_id"],
                    source.operation,
                    source.source_key,
                    source.received_at,
                    source.identity_state,
                    source.payload,
                    source.content_hash,
                    run["external_encounter_id"],
                )
                source_id = (
                    source_row["id"]
                    if source_row
                    else await c.fetchval(
                        """SELECT id FROM clinical.source_record WHERE hospital_id=$1 AND patient_reference_id=$2
                  AND source_operation=$3 AND source_record_key=$4 AND content_hash=$5""",
                        run["hospital_id"],
                        run["patient_reference_id"],
                        source.operation,
                        source.source_key,
                        source.content_hash,
                    )
                )
                source_ids.append(source_id)
            number = await c.fetchval(
                "SELECT coalesce(max(snapshot_no),0)+1 FROM clinical.clinical_snapshot WHERE encounter_reference_id=$1",
                run["encounter_reference_id"],
            )
            saved = await insert(
                c,
                "clinical.clinical_snapshot",
                dict(
                    created_by_principal=run["created_by_principal"],
                    hospital_id=run["hospital_id"],
                    patient_reference_id=run["patient_reference_id"],
                    encounter_reference_id=run["encounter_reference_id"],
                    snapshot_no=number,
                    snapshot_schema_version="facts.v1",
                    clinical_payload=snapshot.model_dump(mode="json"),
                    collection_started_at=started,
                    collection_finished_at=datetime.now(UTC),
                    builder_version="snapshot-builder.v1",
                    content_hash=fingerprint(snapshot),
                    usage_mode="TEST_ONLY",
                    quality_summary={"fact_count": len(snapshot.facts)},
                ),
            )
            for source_id in set(source_ids):
                await insert(
                    c,
                    "clinical.snapshot_source",
                    dict(
                        created_by_principal=run["created_by_principal"],
                        snapshot_id=saved["id"],
                        source_record_id=source_id,
                        usage_paths=["facts", "text_records"],
                        source_role="HOSPITAL_SOURCE",
                    ),
                )
            await c.execute(
                "UPDATE clinical.prepare_run SET snapshot_id=$2,stage='MATCHING' WHERE id=$1",
                run["id"],
                saved["id"],
            )
            disease = snapshot.facts.get("disease")
            plans, manifest = await load_inputs(
                c,
                str(disease.value) if disease and disease.status == "CONFIRMED" else "",
                "TEST_ONLY",
            )
        result = assess(snapshot, plans, datetime.now(UTC), "TEST_ONLY")
        if manifest.get("configuration_state"):
            result.notices.append(
                Finding(
                    code=manifest["configuration_state"],
                    message="尚无已配置的疾病/病理适用关系，不能解释为临床无适用方案",
                )
            )
            if result.outcome_code == "NO_CANDIDATE":
                result.outcome_code = "NEEDS_REVIEW"
        catalog = PostgresCatalogReader(self.pool)
        details = {}
        for candidate in result.candidates:
            detail = await catalog.get_regimen(candidate.regimen_id, candidate.version_id)
            if not detail:
                raise BusinessError("FIXED_TEMPLATE_MISSING", "候选的固定版本不可读取", 503)
            details[candidate.version_id] = detail
        async with self.pool.acquire() as c, c.transaction():
            await self.fenced(c, job)
            context = await c.fetchrow(
                "SELECT * FROM clinical.launch_context WHERE id=$1 FOR UPDATE",
                run["launch_context_id"],
            )
            if (
                context["context_state"] != "ACTIVE"
                or context["active_generation"] != run["generation"]
            ):
                await c.execute(
                    "UPDATE clinical.prepare_run SET status='SUPERSEDED',completed_at=now() WHERE id=$1",
                    run["id"],
                )
                await c.execute("UPDATE ops.job SET status='CANCELLED' WHERE id=$1", job["id"])
                return
            decision = await insert(
                c,
                "clinical.decision_run",
                dict(
                    created_by_principal=run["created_by_principal"],
                    launch_context_id=run["launch_context_id"],
                    snapshot_id=saved["id"],
                    prepare_run_id=run["id"],
                    purpose="MATCH_CANDIDATES",
                    input_hash=fingerprint(snapshot),
                    input_contract_version="facts.v1",
                    capability_manifest={"matching": "DETERMINISTIC_V2_2"},
                    orchestrator_version="patient-flow.v1",
                    usage_mode="TEST_ONLY",
                    knowledge_manifest=manifest,
                    manifest_hash=fingerprint(manifest),
                    status="SUCCEEDED",
                    release_gate_state="UNVERIFIED",
                    output_contract_version="decision.v1",
                    outcome_code=result.outcome_code,
                    output_payload=result.model_dump(mode="json"),
                    output_hash=fingerprint(result),
                    started_at=started,
                    completed_at=datetime.now(UTC),
                ),
            )
            for assessment in result.candidates:
                template = await fixed_projection(
                    c,
                    run["hospital_id"],
                    details[assessment.version_id],
                    run["created_by_principal"],
                )
                data = assessment.model_dump(mode="json")
                await insert(
                    c,
                    "clinical.decision_candidate",
                    dict(
                        created_by_principal=run["created_by_principal"],
                        decision_run_id=decision["id"],
                        candidate_key=str(assessment.version_id),
                        template_ref_id=template,
                        mapping_state="MATCHED",
                        classification_schema="PHASE0_V2_2",
                        presentation_region=assessment.presentation_region,
                        evidence_state=assessment.evidence_state,
                        evidence_level=assessment.evidence_level,
                        evidence_grade=assessment.evidence_grade,
                        selected_evidence_ref=data["selected_evidence_ref"],
                        evidence_refs=data["evidence_refs"],
                        rank_group=assessment.evidence_level
                        if assessment.presentation_region == "RECOMMENDATION"
                        and assessment.evidence_level
                        else None,
                        data_labels=data["data_labels"],
                        safety_labels=data["safety_labels"],
                        x_reason_code=assessment.x_reason_code,
                        x_basis=data["x_basis"],
                        applicability_snapshot=data["applicability"],
                        explanation_summary={"regimen_code": assessment.regimen_code},
                        candidate_hash=fingerprint(assessment),
                    ),
                )
            await c.execute(
                "UPDATE clinical.prepare_run SET status='SUCCEEDED',stage='READY',completed_at=now(),source_completion=$2 WHERE id=$1",
                run["id"],
                {"source_count": len(sources), "snapshot_id": str(saved["id"])},
            )
            await c.execute(
                "UPDATE ops.job SET status='SUCCEEDED',lease_expires_at=NULL WHERE id=$1", job["id"]
            )

    async def once(self):
        job = await self.claim()
        if not job:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(job))
        try:
            if job["job_kind"] == "PREPARE":
                await self.prepare(job)
            elif self.agent_handler:
                await self.agent_handler(job, self)
            else:
                raise BusinessError("AGENT_NOT_CONFIGURED", "智能体执行器尚未配置", 503)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "HOSPITAL_TIMEOUT"
                if isinstance(exc, httpx.TimeoutException)
                else "TASK_FAILED"
            )
            retry = (
                isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
                and job["attempt_count"] < job["max_attempts"]
            )
            async with self.pool.acquire() as c, c.transaction():
                try:
                    await self.fenced(c, job)
                except BusinessError:
                    return True
                await c.execute(
                    "UPDATE ops.job SET status=$2,last_error_code=$3,available_at=$4,lease_expires_at=NULL WHERE id=$1",
                    job["id"],
                    "RETRY_WAIT" if retry else "FAILED",
                    code,
                    datetime.now(UTC) + timedelta(seconds=2 ** job["attempt_count"]),
                )
                if job["prepare_run_id"]:
                    await c.execute(
                        "UPDATE clinical.prepare_run SET status=$2,error_code=$3,error_summary=$4,completed_at=now() WHERE id=$1 AND status<>'SUPERSEDED'",
                        job["prepare_run_id"],
                        "QUEUED" if retry else "FAILED",
                        code,
                        f"job:{job['id']}",
                    )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def loop(self):
        while True:
            worked = await self.once()
            if not worked:
                await asyncio.sleep(self.settings.worker_poll_seconds)
