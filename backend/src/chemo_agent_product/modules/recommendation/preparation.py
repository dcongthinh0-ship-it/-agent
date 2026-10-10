from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from chemo_agent_product.core.domain import Finding, fingerprint
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.integrations.hospital.reader import ConfiguredHospitalReader
from chemo_agent_product.modules.knowledge.service import fixed_projection
from chemo_agent_product.modules.recommendation.matching import assess
from chemo_agent_product.modules.regimen.reader import PostgresCatalogReader

from . import preparation_repository as repository


class PreparationService:
    """Hospital collection, fixed snapshots and deterministic candidate preparation."""

    def __init__(self, worker):
        self.pool = worker.pool
        self.settings = worker.settings
        self.reader = worker.reader
        self.knowledge_loader = worker.knowledge_loader
        self.check_lease = worker.fenced

    async def prepare(self, job):
        async with self.pool.acquire() as c, c.transaction():
            await self.check_lease(c, job)
            run = await repository.get_locked_preparation(c, job["prepare_run_id"])
            if (
                run["context_state"] != "ACTIVE"
                or run["generation"] != run["active_generation"]
                or run["expires_at"] <= datetime.now(UTC)
            ):
                await repository.supersede_preparation(c, run["id"])
                await repository.cancel_job(c, job["id"])
                return
            await repository.start_preparation(c, run["id"])
        started = datetime.now(UTC)

        async def observe(event):
            async with self.pool.acquire() as c, c.transaction():
                if event["phase"] == "STARTED":
                    await self.check_lease(c, job)
                    attempt = await repository.insert_hospital_call_attempt(
                        c,
                        dict(
                            created_by_principal=run["created_by_principal"],
                            hospital_id=run["hospital_id"],
                            prepare_run_id=run["id"],
                            call_group_id=UUID(event["call_group_id"]),
                            attempt_no=event["attempt_no"],
                            operation_name=event["operation_name"],
                            request_id=event["request_id"],
                            contract_version=event["contract_version"],
                            started_at=event["started_at"],
                            request_hash=event["request_hash"],
                            transport_outcome="STARTED",
                        ),
                    )
                    return attempt["id"]
                # Late network completion is still audited, but never changes a snapshot.
                await repository.complete_hospital_attempt(
                    c,
                    event["request_id"],
                    run["id"],
                    run["hospital_id"],
                    event["completed_at"],
                    event["transport_outcome"],
                    event.get("http_status"),
                    event.get("business_code"),
                    event.get("response_hash"),
                    event.get("safe_error_summary"),
                )
                return None

        args = (run["external_patient_id"], run["external_encounter_id"], run["external_staff_id"])
        sources = (
            await self.reader.fetch(*args, observer=observe)
            if isinstance(self.reader, ConfiguredHospitalReader)
            else await self.reader.fetch(*args)
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
            await self.check_lease(c, job)
            context = await repository.get_locked_context(c, run["launch_context_id"])
            if (
                context["context_state"] != "ACTIVE"
                or context["active_generation"] != run["generation"]
                or context["expires_at"] <= datetime.now(UTC)
            ):
                await repository.supersede_preparation(c, run["id"])
                await repository.cancel_job(c, job["id"])
                return
            await repository.lock_snapshot(c, f"snapshot:{run['encounter_reference_id']}")
            source_ids = []
            for source in sources:
                source_row = await repository.create_source_record(
                    c,
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
                    source.request_attempt_id,
                )
                source_id = (
                    source_row["id"]
                    if source_row
                    else await repository.find_source_record(
                        c,
                        run["hospital_id"],
                        run["patient_reference_id"],
                        source.operation,
                        source.source_key,
                        source.content_hash,
                    )
                )
                source_ids.append(source_id)
            number = await repository.next_snapshot_number(c, run["encounter_reference_id"])
            saved = await repository.insert_clinical_snapshot(
                c,
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
                await repository.insert_snapshot_source(
                    c,
                    dict(
                        created_by_principal=run["created_by_principal"],
                        snapshot_id=saved["id"],
                        source_record_id=source_id,
                        usage_paths=["facts", "text_records"],
                        source_role="HOSPITAL_SOURCE",
                    ),
                )
            await repository.start_matching(c, run["id"], saved["id"])
            disease = snapshot.facts.get("disease")
            plans, manifest = await self.knowledge_loader(
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
            await self.check_lease(c, job)
            context = await repository.get_locked_context(c, run["launch_context_id"])
            if (
                context["context_state"] != "ACTIVE"
                or context["active_generation"] != run["generation"]
            ):
                await repository.supersede_preparation(c, run["id"])
                await repository.cancel_job(c, job["id"])
                return
            decision = await repository.insert_decision_run(
                c,
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
                await repository.insert_decision_candidate(
                    c,
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
            await repository.complete_preparation(
                c, run["id"], {"source_count": len(sources), "snapshot_id": str(saved["id"])}
            )
            if self.settings.model_configured:
                from chemo_agent_product.agent_runtime.contracts import AgentRequest
                from chemo_agent_product.agent_runtime.service import AgentService
                from chemo_agent_product.core.security import Principal
                from chemo_agent_product.modules.clinical_context.workflow import Workflow

                actor = Principal(
                    subject=run["created_by_principal"],
                    hospital_id=run["hospital_id"],
                    staff_id=run["operator_staff_id"],
                    roles=["DOCTOR"],
                    expires_at=int(run["expires_at"].timestamp()),
                )
                # Queue atomically with prepared results. Opening the widget is
                # not the trigger, and a worker restart cannot lose the request.
                await AgentService(Workflow(self.pool, self.settings)).enqueue_at(
                    c,
                    actor,
                    run["launch_context_id"],
                    AgentRequest(kind="RECOMMENDATION"),
                    f"auto-main:{run['id']}",
                )
            await repository.complete_job(c, job["id"])
