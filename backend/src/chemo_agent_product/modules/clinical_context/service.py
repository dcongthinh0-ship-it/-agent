from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from chemo_agent_product.core.domain import fingerprint
from chemo_agent_product.core.security import BusinessError, Principal, require_role
from chemo_agent_product.modules.clinical_context import repository as repository
from chemo_agent_product.modules.clinical_context.schemas import LaunchInput


class ClinicalContextService:
    def __init__(self, workflow):
        self.workflow = workflow

    async def scoped_context(
        self,
        c: asyncpg.Connection,
        context_id: UUID,
        p: Principal,
        active: bool = True,
        lock: bool = False,
    ):
        require_role(p, "DOCTOR")
        row = await repository.get_scoped_context(
            c, context_id, p.hospital_id, p.staff_id, lock=lock
        )
        if not row:
            raise BusinessError("CONTEXT_FORBIDDEN", "无权读取该患者上下文", 403)
        if (p.patient_id and p.patient_id != row["patient_reference_id"]) or (
            p.encounter_id and p.encounter_id != row["encounter_reference_id"]
        ):
            raise BusinessError("PATIENT_SCOPE_MISMATCH", "当前授权与患者或就诊不一致", 403)
        if active and (row["context_state"] != "ACTIVE" or row["expires_at"] <= datetime.now(UTC)):
            raise BusinessError("CONTEXT_EXPIRED", "本次患者上下文已失效，请从工作站重新进入", 409)
        return row

    async def prepare(self, c, context, p, generation):
        run = await repository.insert_prepare_run(
            c,
            dict(
                created_by_principal=p.subject,
                launch_context_id=context["id"],
                generation=generation,
                trigger_kind="AUTO_PREPARE",
                query_profile_version="hospital-adapter.v1",
                status="QUEUED",
                stage="FETCHING",
            ),
        )
        await repository.set_current_preparation(c, context["id"], run["id"], generation)
        await repository.insert_job(
            c,
            dict(
                created_by_principal=p.subject,
                hospital_id=p.hospital_id,
                job_kind="PREPARE",
                prepare_run_id=run["id"],
                dedupe_key=f"prepare:{run['id']}",
                available_at=await repository.database_time(c),
                max_attempts=3,
                status="QUEUED",
            ),
        )
        return run["id"]

    async def launch(self, p: Principal, request: LaunchInput, key: str):
        require_role(p, "DOCTOR")
        async with self.workflow.pool.acquire() as c, c.transaction():
            cmd, cached = await self.workflow.command(
                c, p, "LAUNCH", key, request.model_dump(mode="json")
            )
            if cached:
                return cached
            hospital = await repository.get_hospital(c, p.hospital_id)
            staff = await repository.get_staff(c, p.staff_id, p.hospital_id)
            if not hospital or not staff or staff["external_staff_id"] != request.operator_id:
                raise BusinessError(
                    "HOSPITAL_IDENTITY_NOT_CONFIGURED", "可信医院或操作医生映射尚未配置", 503
                )
            if hospital["status"] != "TEST_ONLY":
                raise BusinessError("CLINICAL_AUTH_NOT_CONFIGURED", "真实院方身份适配尚未配置", 503)
            # Serialize one host session, including requests which reach the server
            # out of order before either has returned a context to the browser.
            await repository.lock_session(
                c, f"launch:{p.hospital_id}:{p.staff_id}:{request.session_scope_ref}"
            )
            latest = await repository.get_previous_context(
                c, p.hospital_id, p.staff_id, request.session_scope_ref
            )
            same_patient = bool(
                latest
                and latest["external_patient_id"] == request.patient_id
                and latest["external_encounter_id"] == request.encounter_id
            )
            generation = request.client_generation or (
                latest["host_generation"] + 1 if latest else 1
            )
            if latest and request.client_generation and generation < latest["host_generation"]:
                raise BusinessError("LAUNCH_SUPERSEDED", "该请求属于已切换的患者，已停止处理", 409)
            if request.request_scene == "ASSISTANT_OPEN" or (
                latest and request.client_generation and generation == latest["host_generation"]
            ):
                if (
                    not same_patient
                    or not latest
                    or latest["context_state"] != "ACTIVE"
                    or latest["expires_at"] <= datetime.now(UTC)
                ):
                    raise BusinessError(
                        "PREPARE_CONTEXT_REQUIRED", "当前患者尚无有效后台准备任务", 409
                    )
                await self.workflow.scoped_context(c, latest["id"], p)
                result = {
                    "context_id": str(latest["id"]),
                    "launch_url": f"/?context_id={latest['id']}",
                    "prepare_status": "EXISTING",
                    "patient_id": request.patient_id,
                    "encounter_id": request.encounter_id,
                }
                await self.workflow.complete(c, cmd, result, "launch_context", latest["id"])
                return result
            patient = await repository.create_patient_reference(
                c, p.subject, p.hospital_id, request.patient_id
            )
            patient = patient or await repository.get_patient_reference(
                c, p.hospital_id, request.patient_id
            )
            encounter = await repository.create_encounter_reference(
                c, p.subject, p.hospital_id, patient["id"], request.encounter_id
            )
            encounter = encounter or await repository.get_encounter_reference(
                c, p.hospital_id, request.encounter_id
            )
            if (
                encounter["patient_reference_id"] != patient["id"]
                or (p.patient_id and p.patient_id != patient["id"])
                or (p.encounter_id and p.encounter_id != encounter["id"])
            ):
                raise BusinessError("PATIENT_SCOPE_MISMATCH", "患者与就诊归属不一致", 403)
            await repository.supersede_session_contexts(
                c, p.hospital_id, p.staff_id, request.session_scope_ref
            )
            context = await repository.insert_launch_context(
                c,
                dict(
                    created_by_principal=p.subject,
                    hospital_id=p.hospital_id,
                    patient_reference_id=patient["id"],
                    encounter_reference_id=encounter["id"],
                    operator_staff_id=p.staff_id,
                    dept_code=request.dept_code,
                    request_scene=request.request_scene,
                    authz_reference="local-test-hmac.v1",
                    access_scope_hash=fingerprint(p.model_dump(mode="json")),
                    expires_at=datetime.now(UTC)
                    + timedelta(seconds=self.workflow.settings.context_ttl_seconds),
                    session_scope_ref=request.session_scope_ref,
                    host_generation=generation,
                ),
            )
            await self.workflow.prepare(c, context, p, 1)
            result = {
                "context_id": str(context["id"]),
                "launch_url": f"/?context_id={context['id']}",
                "prepare_status": "QUEUED",
                "patient_id": request.patient_id,
                "encounter_id": request.encounter_id,
            }
            await self.workflow.audit(
                c, p, context, "AUTO_PREPARE_QUEUED", context["id"], {"generation": 1}
            )
            await self.workflow.complete(c, cmd, result, "launch_context", context["id"])
            return result

    async def preparation_status(self, p: Principal, context_id: UUID):
        async with self.workflow.pool.acquire() as c, c.transaction(readonly=True):
            context = await self.workflow.scoped_context(c, context_id, p, active=False)
            decision = await repository.get_preparation_decision_status(
                c, context_id, context["current_prepare_run_id"]
            )
        return {
            "context_id": str(context_id),
            "context_state": context["context_state"],
            "expires_at": context["expires_at"].isoformat(),
            "generation": context["active_generation"],
            "prepare_status": context["prepare_status"],
            "prepare_stage": context["prepare_stage"],
            "decision_status": decision["status"] if decision else None,
            "candidate_count": decision["candidate_count"] if decision else 0,
        }

    async def read_context(self, p: Principal, context_id: UUID):
        async with self.workflow.pool.acquire() as c, c.transaction(readonly=True):
            context = await self.workflow.scoped_context(c, context_id, p, active=False)
            decision = await repository.get_current_decision(
                c, context_id, context["current_prepare_run_id"]
            )
            candidates = (
                await repository.list_current_candidates(c, decision["id"])
                if decision and decision["status"] == "SUCCEEDED"
                else []
            )
            snapshot = (
                await repository.get_snapshot(c, context["snapshot_id"])
                if context["snapshot_id"]
                else None
            )
            instances = await repository.list_encounter_instances(
                c,
                p.hospital_id,
                context["patient_reference_id"],
                context["encounter_reference_id"],
                context_id,
                context["snapshot_id"],
            )
        return dict(
            context_id=str(context_id),
            mode="TEST_ONLY",
            context_state=context["context_state"],
            expires_at=context["expires_at"].isoformat(),
            generation=context["active_generation"],
            prepare_run_id=str(context["current_prepare_run_id"])
            if context["current_prepare_run_id"]
            else None,
            prepare_status=context["prepare_status"],
            prepare_stage=context["prepare_stage"],
            prepare_error_code=context["prepare_error_code"],
            decision_run_id=str(decision["id"]) if decision else None,
            decision_status=decision["status"] if decision else None,
            outcome_code=decision["outcome_code"] if decision else None,
            patient_ref=context["external_patient_id"],
            encounter_ref=context["external_encounter_id"],
            snapshot_id=str(snapshot["id"]) if snapshot else None,
            snapshot=snapshot["clinical_payload"] if snapshot else None,
            instances=[dict(r) for r in instances],
            candidates=[{**dict(r), "candidate_id": r["id"]} for r in candidates],
            notices=decision["output_payload"].get("notices", [])
            if decision and decision["output_payload"]
            else [],
        )

    async def refresh(self, p, context_id, key, expected_generation):
        async with self.workflow.pool.acquire() as c, c.transaction():
            context = await self.workflow.scoped_context(c, context_id, p, lock=True)
            cmd, cached = await self.workflow.command(
                c,
                p,
                "REFRESH",
                key,
                {"context_id": str(context_id), "generation": expected_generation},
            )
            if cached:
                return cached
            if context["active_generation"] != expected_generation:
                raise BusinessError("GENERATION_CONFLICT", "已有新的准备任务，请刷新页面", 409)
            await repository.supersede_preparation(c, context["current_prepare_run_id"])
            run_id = await self.workflow.prepare(c, context, p, expected_generation + 1)
            result = {"prepare_run_id": str(run_id), "generation": expected_generation + 1}
            await self.workflow.audit(c, p, context, "REFRESH_QUEUED", run_id, result)
            await self.workflow.complete(c, cmd, result, "prepare_run", run_id)
            return result
