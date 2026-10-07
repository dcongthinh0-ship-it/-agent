"""Scoped, durable patient operations. Every mutation is an idempotent transaction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg

from chemo_agent_product.config import Settings
from chemo_agent_product.database import insert
from chemo_agent_product.domain import PatientSnapshot, fingerprint
from chemo_agent_product.patient_contracts import ConfirmInput, LaunchInput, SaveInput
from chemo_agent_product.security import BusinessError, Principal, require_role

FACT_FIELDS = {
    "patient_name": "patient_name",
    "birth_date": "birth_date",
    "medical_record_number": "medical_record_number",
    "diagnosis": "diagnosis",
    "allergy_history": "allergy_history",
    "height_cm": "height",
    "weight_kg": "weight",
    "ecog_score": "ecog",
    "wbc": "wbc",
    "anc": "anc",
    "platelets": "platelets",
    "alt": "alt",
    "ast": "ast",
    "total_bilirubin": "total_bilirubin",
    "serum_creatinine": "creatinine",
}


class Workflow:
    def __init__(self, pool: asyncpg.Pool, settings: Settings):
        self.pool, self.settings = pool, settings

    async def scoped_context(
        self,
        c: asyncpg.Connection,
        context_id: UUID,
        p: Principal,
        active: bool = True,
        lock: bool = False,
    ):
        require_role(p, "DOCTOR")
        row = await c.fetchrow(
            """SELECT lc.*,pr.snapshot_id,pr.status AS prepare_status,
          pr.stage AS prepare_stage,pr.error_code AS prepare_error_code,
          pat.external_patient_id,enc.external_encounter_id,s.external_staff_id
          FROM clinical.launch_context lc JOIN clinical.patient_reference pat ON pat.id=lc.patient_reference_id
          JOIN clinical.encounter_reference enc ON enc.id=lc.encounter_reference_id
          JOIN clinical.staff_reference s ON s.id=lc.operator_staff_id
          LEFT JOIN clinical.prepare_run pr ON pr.id=lc.current_prepare_run_id AND pr.generation=lc.active_generation
          WHERE lc.id=$1 AND lc.hospital_id=$2 AND lc.operator_staff_id=$3"""
            + (" FOR UPDATE OF lc" if lock else ""),
            context_id,
            p.hospital_id,
            p.staff_id,
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

    async def command(self, c, p, operation, key, payload):
        if not key or len(key) > 128:
            raise BusinessError("IDEMPOTENCY_KEY_REQUIRED", "缺少有效操作标识", 422)
        row = await c.fetchrow(
            """INSERT INTO ops.command_receipt
          (created_by_principal,hospital_id,caller_scope,operation_code,request_key,request_hash,status)
          VALUES($1,$2,$1,$3,$4,$5,'PROCESSING') ON CONFLICT DO NOTHING RETURNING *""",
            p.subject,
            p.hospital_id,
            operation,
            key,
            fingerprint(payload),
        )
        if row:
            return row["id"], None
        old = await c.fetchrow(
            """SELECT * FROM ops.command_receipt
          WHERE hospital_id=$1 AND caller_scope=$2 AND operation_code=$3 AND request_key=$4 FOR UPDATE""",
            p.hospital_id,
            p.subject,
            operation,
            key,
        )
        if old["request_hash"] != fingerprint(payload):
            raise BusinessError("IDEMPOTENCY_CONFLICT", "同一操作标识已用于不同内容", 409)
        if old["status"] == "COMPLETED":
            return old["id"], old["response_summary"]
        raise BusinessError("COMMAND_IN_PROGRESS", "该操作正在处理，请按原操作标识重试", 409)

    async def complete(self, c, command_id, response, resource_type, resource_id):
        await c.execute(
            """UPDATE ops.command_receipt SET status='COMPLETED',response_summary=$2,
          resource_type=$3,resource_id=$4 WHERE id=$1""",
            command_id,
            response,
            resource_type,
            resource_id,
        )

    async def audit(self, c, p, context, event, aggregate_id, summary):
        await insert(
            c,
            "ops.audit_event",
            dict(
                created_by_principal=p.subject,
                event_type=event,
                actor_principal_ref=p.subject,
                hospital_id=p.hospital_id,
                aggregate_type="patient_flow",
                aggregate_id=aggregate_id,
                correlation_id=context["id"],
                event_time=datetime.now(UTC),
                patient_reference_id=context["patient_reference_id"],
                encounter_reference_id=context["encounter_reference_id"],
                safe_summary=summary,
                content_hash=fingerprint(summary),
            ),
        )

    async def prepare(self, c, context, p, generation):
        run = await insert(
            c,
            "clinical.prepare_run",
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
        await c.execute(
            "UPDATE clinical.launch_context SET current_prepare_run_id=$2,active_generation=$3 WHERE id=$1",
            context["id"],
            run["id"],
            generation,
        )
        await insert(
            c,
            "ops.job",
            dict(
                created_by_principal=p.subject,
                hospital_id=p.hospital_id,
                job_kind="PREPARE",
                prepare_run_id=run["id"],
                dedupe_key=f"prepare:{run['id']}",
                available_at=datetime.now(UTC),
                max_attempts=3,
                status="QUEUED",
            ),
        )
        return run["id"]

    async def launch(self, p: Principal, request: LaunchInput, key: str):
        require_role(p, "DOCTOR")
        async with self.pool.acquire() as c, c.transaction():
            cmd, cached = await self.command(c, p, "LAUNCH", key, request.model_dump(mode="json"))
            if cached:
                return cached
            hospital = await c.fetchrow(
                "SELECT * FROM integration.hospital WHERE id=$1", p.hospital_id
            )
            staff = await c.fetchrow(
                "SELECT * FROM clinical.staff_reference WHERE id=$1 AND hospital_id=$2",
                p.staff_id,
                p.hospital_id,
            )
            if not hospital or not staff or staff["external_staff_id"] != request.operator_id:
                raise BusinessError(
                    "HOSPITAL_IDENTITY_NOT_CONFIGURED", "可信医院或操作医生映射尚未配置", 503
                )
            if hospital["status"] != "TEST_ONLY":
                raise BusinessError("CLINICAL_AUTH_NOT_CONFIGURED", "真实院方身份适配尚未配置", 503)
            patient = await c.fetchrow(
                """INSERT INTO clinical.patient_reference
              (created_by_principal,hospital_id,external_patient_id,reference_state) VALUES($1,$2,$3,'UNVERIFIED')
              ON CONFLICT(hospital_id,external_patient_id) DO NOTHING RETURNING *""",
                p.subject,
                p.hospital_id,
                request.patient_id,
            )
            patient = patient or await c.fetchrow(
                "SELECT * FROM clinical.patient_reference WHERE hospital_id=$1 AND external_patient_id=$2",
                p.hospital_id,
                request.patient_id,
            )
            encounter = await c.fetchrow(
                """INSERT INTO clinical.encounter_reference
              (created_by_principal,hospital_id,patient_reference_id,external_encounter_id,last_received_at)
              VALUES($1,$2,$3,$4,now()) ON CONFLICT(hospital_id,external_encounter_id) DO NOTHING RETURNING *""",
                p.subject,
                p.hospital_id,
                patient["id"],
                request.encounter_id,
            )
            encounter = encounter or await c.fetchrow(
                "SELECT * FROM clinical.encounter_reference WHERE hospital_id=$1 AND external_encounter_id=$2",
                p.hospital_id,
                request.encounter_id,
            )
            if (
                encounter["patient_reference_id"] != patient["id"]
                or (p.patient_id and p.patient_id != patient["id"])
                or (p.encounter_id and p.encounter_id != encounter["id"])
            ):
                raise BusinessError("PATIENT_SCOPE_MISMATCH", "患者与就诊归属不一致", 403)
            if request.request_scene == "ASSISTANT_OPEN":
                previous = await c.fetchrow(
                    """SELECT * FROM clinical.launch_context WHERE hospital_id=$1
                  AND patient_reference_id=$2 AND encounter_reference_id=$3 AND operator_staff_id=$4
                  AND session_scope_ref=$5 AND context_state='ACTIVE' AND expires_at>now()
                  ORDER BY created_at DESC LIMIT 1""",
                    p.hospital_id,
                    patient["id"],
                    encounter["id"],
                    p.staff_id,
                    request.session_scope_ref,
                )
                if previous:
                    result = {
                        "context_id": str(previous["id"]),
                        "launch_url": f"/?context_id={previous['id']}",
                        "prepare_status": "EXISTING",
                        "patient_id": request.patient_id,
                        "encounter_id": request.encounter_id,
                    }
                    await self.complete(c, cmd, result, "launch_context", previous["id"])
                    return result
            if request.previous_context_id:
                await c.execute(
                    """UPDATE clinical.launch_context SET context_state='SUPERSEDED'
                  WHERE id=$1 AND hospital_id=$2 AND operator_staff_id=$3 AND session_scope_ref=$4
                  AND context_state='ACTIVE'""",
                    request.previous_context_id,
                    p.hospital_id,
                    p.staff_id,
                    request.session_scope_ref,
                )
            context = await insert(
                c,
                "clinical.launch_context",
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
                    + timedelta(seconds=self.settings.context_ttl_seconds),
                    session_scope_ref=request.session_scope_ref,
                ),
            )
            await self.prepare(c, context, p, 1)
            result = {
                "context_id": str(context["id"]),
                "launch_url": f"/?context_id={context['id']}",
                "prepare_status": "QUEUED",
                "patient_id": request.patient_id,
                "encounter_id": request.encounter_id,
            }
            await self.audit(c, p, context, "AUTO_PREPARE_QUEUED", context["id"], {"generation": 1})
            await self.complete(c, cmd, result, "launch_context", context["id"])
            return result

    async def read_context(self, p: Principal, context_id: UUID):
        async with self.pool.acquire() as c, c.transaction(readonly=True):
            context = await self.scoped_context(c, context_id, p, active=False)
            decision = await c.fetchrow(
                """SELECT * FROM clinical.decision_run WHERE launch_context_id=$1
              AND prepare_run_id=$2 AND purpose='MATCH_CANDIDATES' ORDER BY created_at DESC,id DESC LIMIT 1""",
                context_id,
                context["current_prepare_run_id"],
            )
            candidates = (
                await c.fetch(
                    """SELECT cand.*,t.external_regimen_id AS regimen_id,
              t.external_regimen_version_id AS version_id,t.regimen_code,t.template_payload->>'display_name' AS display_name
              FROM clinical.decision_candidate cand JOIN catalog_bridge.template_version_reference t ON t.id=cand.template_ref_id
              WHERE cand.decision_run_id=$1 ORDER BY CASE presentation_region WHEN 'RECOMMENDATION' THEN 0 ELSE 1 END,
              evidence_level NULLS LAST,t.regimen_code,cand.id""",
                    decision["id"],
                )
                if decision and decision["status"] == "SUCCEEDED"
                else []
            )
            snapshot = (
                await c.fetchrow(
                    "SELECT * FROM clinical.clinical_snapshot WHERE id=$1", context["snapshot_id"]
                )
                if context["snapshot_id"]
                else None
            )
            instances = await c.fetch(
                """SELECT id,row_version,current_revision_id,current_confirmed_revision_id
              FROM clinical.patient_regimen_instance WHERE origin_context_id=$1 ORDER BY created_at DESC""",
                context_id,
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
        async with self.pool.acquire() as c, c.transaction():
            context = await self.scoped_context(c, context_id, p, lock=True)
            cmd, cached = await self.command(
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
            await c.execute(
                "UPDATE clinical.prepare_run SET status='SUPERSEDED' WHERE id=$1 AND status IN ('QUEUED','RUNNING')",
                context["current_prepare_run_id"],
            )
            run_id = await self.prepare(c, context, p, expected_generation + 1)
            result = {"prepare_run_id": str(run_id), "generation": expected_generation + 1}
            await self.audit(c, p, context, "REFRESH_QUEUED", run_id, result)
            await self.complete(c, cmd, result, "prepare_run", run_id)
            return result

    async def candidate(self, c, context_id, p, candidate_id):
        context = await self.scoped_context(c, context_id, p)
        row = await c.fetchrow(
            """SELECT dc.*,t.template_payload,t.content_hash AS template_hash,
          dr.snapshot_id,dr.knowledge_manifest FROM clinical.decision_candidate dc
          JOIN clinical.decision_run dr ON dr.id=dc.decision_run_id AND dr.status='SUCCEEDED'
          JOIN catalog_bridge.template_version_reference t ON t.id=dc.template_ref_id
          WHERE dc.id=$1 AND dr.launch_context_id=$2 AND dr.prepare_run_id=$3""",
            candidate_id,
            context_id,
            context["current_prepare_run_id"],
        )
        if not row:
            raise BusinessError("CANDIDATE_STALE", "候选不属于当前准备结果，请刷新", 409)
        return context, row

    async def detail(self, p, context_id, candidate_id):
        async with self.pool.acquire() as c, c.transaction(readonly=True):
            context, row = await self.candidate(c, context_id, p, candidate_id)
            snapshot = await c.fetchval(
                "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1",
                row["snapshot_id"],
            )
        return {
            "candidate_id": str(row["id"]),
            "template": row["template_payload"],
            "template_hash": row["template_hash"],
            "snapshot": snapshot,
            "assessment": dict(row),
        }

    async def select(self, p, context_id, candidate_id, key):
        async with self.pool.acquire() as c, c.transaction():
            context, row = await self.candidate(c, context_id, p, candidate_id)
            cmd, cached = await self.command(
                c,
                p,
                "SELECT",
                key,
                {"context_id": str(context_id), "candidate_id": str(candidate_id)},
            )
            if cached:
                return cached
            if row["presentation_region"] != "RECOMMENDATION":
                raise BusinessError(
                    "X_CANDIDATE_NOT_SELECTABLE", "X 区方案仅供查看依据，不能直接采用", 409
                )
            instance = await insert(
                c,
                "clinical.patient_regimen_instance",
                dict(
                    created_by_principal=p.subject,
                    hospital_id=p.hospital_id,
                    patient_reference_id=context["patient_reference_id"],
                    encounter_reference_id=context["encounter_reference_id"],
                    template_ref_id=row["template_ref_id"],
                    origin_context_id=context_id,
                    origin_type="RECOMMENDATION",
                    origin_candidate_id=candidate_id,
                ),
            )
            await insert(
                c,
                "clinical.doctor_action_event",
                dict(
                    created_by_principal=p.subject,
                    launch_context_id=context_id,
                    actor_staff_id=p.staff_id,
                    command_receipt_id=cmd,
                    instance_id=instance["id"],
                    candidate_id=candidate_id,
                    action_type="SELECT",
                    acted_at=datetime.now(UTC),
                ),
            )
            result = {"instance_id": str(instance["id"]), "row_version": instance["row_version"]}
            await self.audit(c, p, context, "CANDIDATE_SELECTED", instance["id"], result)
            await self.complete(c, cmd, result, "patient_regimen_instance", instance["id"])
            return result

    async def instance(self, c, context_id, p, instance_id, lock=False):
        context = await self.scoped_context(c, context_id, p)
        row = await c.fetchrow(
            """SELECT i.*,t.template_payload,t.content_hash AS template_hash,t.availability_state,
          cand.applicability_snapshot,cand.evidence_refs,cand.data_labels,cand.safety_labels,cand.evidence_state,
          d.knowledge_manifest FROM clinical.patient_regimen_instance i
          JOIN catalog_bridge.template_version_reference t ON t.id=i.template_ref_id
          LEFT JOIN clinical.decision_candidate cand ON cand.id=i.origin_candidate_id
          LEFT JOIN clinical.decision_run d ON d.id=cand.decision_run_id
          WHERE i.id=$1 AND i.origin_context_id=$2"""
            + (" FOR UPDATE OF i" if lock else ""),
            instance_id,
            context_id,
        )
        if not row:
            raise BusinessError("INSTANCE_FORBIDDEN", "患者方案不属于本次上下文", 403)
        return context, row

    def initial_fields(self, template, snapshot):
        result = {}
        for field in template["fields"]:
            key = field["field_key"]
            fact = snapshot.facts.get(FACT_FIELDS.get(key, key))
            if field["source_type"] == "HIS" and fact and fact.status == "CONFIRMED":
                result[key] = fact.value
            elif field.get("default_value") is not None:
                result[key] = field["default_value"]
        return result

    async def read_instance(self, p, context_id, instance_id):
        async with self.pool.acquire() as c, c.transaction(readonly=True):
            context, row = await self.instance(c, context_id, p, instance_id)
            rev = (
                await c.fetchrow(
                    "SELECT * FROM clinical.patient_regimen_revision WHERE id=$1",
                    row["current_revision_id"],
                )
                if row["current_revision_id"]
                else None
            )
            snapshot_id = rev["snapshot_id"] if rev else context["snapshot_id"]
            payload = await c.fetchval(
                "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1", snapshot_id
            )
            if not payload:
                raise BusinessError("SNAPSHOT_NOT_READY", "患者数据尚未准备完成", 409)
            values = (
                await c.fetch(
                    "SELECT field_key,value_json FROM clinical.patient_regimen_field_value WHERE revision_id=$1",
                    rev["id"],
                )
                if rev
                else []
            )
            history = await c.fetch(
                "SELECT id,revision_no,content_hash,created_at,change_reason FROM clinical.patient_regimen_revision WHERE instance_id=$1 ORDER BY revision_no DESC",
                instance_id,
            )
        return dict(
            instance_id=str(instance_id),
            row_version=row["row_version"],
            template=row["template_payload"],
            snapshot=payload,
            field_values={r["field_key"]: r["value_json"] for r in values}
            if rev
            else self.initial_fields(
                row["template_payload"], PatientSnapshot.model_validate(payload)
            ),
            medication_values=rev["selection_manifest"].get("medication_values", {}) if rev else {},
            revision_id=str(rev["id"]) if rev else None,
            revision_hash=rev["content_hash"] if rev else None,
            confirmed_revision_id=str(row["current_confirmed_revision_id"])
            if row["current_confirmed_revision_id"]
            else None,
            compilation_state=rev["compilation_state"] if rev else "NOT_COMPILED",
            history=[dict(r) for r in history],
            data_labels=row["data_labels"] or [],
            safety_labels=row["safety_labels"] or [],
        )

    async def save(self, p, context_id, instance_id, request: SaveInput, key):
        from chemo_agent_product.editor import compile_revision

        async with self.pool.acquire() as c, c.transaction():
            context, row = await self.instance(c, context_id, p, instance_id, lock=True)
            cmd, cached = await self.command(
                c,
                p,
                "SAVE",
                key,
                {
                    "context_id": str(context_id),
                    "instance_id": str(instance_id),
                    **request.model_dump(mode="json"),
                },
            )
            if cached:
                return cached
            if (
                row["row_version"] != request.expected_row_version
                or row["current_revision_id"] != request.base_revision_id
            ):
                raise BusinessError("REVISION_CONFLICT", "方案已有新修订，请重新读取后修改", 409)
            if request.base_revision_id and not request.change_reason:
                raise BusinessError("CHANGE_REASON_REQUIRED", "修改已保存方案时请填写修改原因", 422)
            snapshot_payload = await c.fetchval(
                "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1",
                context["snapshot_id"],
            )
            if not snapshot_payload:
                raise BusinessError("SNAPSHOT_NOT_READY", "患者数据尚未准备完成", 409)
            snapshot = PatientSnapshot.model_validate(snapshot_payload)
            compiled = compile_revision(
                row["template_payload"],
                snapshot,
                request,
                self.initial_fields(row["template_payload"], snapshot),
                (row["knowledge_manifest"] or {}).get("calculation", {}),
            )
            revision_no = await c.fetchval(
                "SELECT coalesce(max(revision_no),0)+1 FROM clinical.patient_regimen_revision WHERE instance_id=$1",
                instance_id,
            )
            manifest = {
                **compiled["manifest"],
                "template_hash": row["template_hash"],
                "evidence_refs": row["evidence_refs"] or [],
                "knowledge_manifest": row["knowledge_manifest"] or {},
                "applicability": row["applicability_snapshot"] or {},
            }
            revision_hash = fingerprint(
                {
                    "snapshot_hash": fingerprint(snapshot),
                    "fields": compiled["fields"],
                    "orders": compiled["orders"],
                    "manifest": manifest,
                    "base": str(request.base_revision_id),
                }
            )
            revision = await insert(
                c,
                "clinical.patient_regimen_revision",
                dict(
                    created_by_principal=p.subject,
                    instance_id=instance_id,
                    revision_no=revision_no,
                    base_revision_id=request.base_revision_id,
                    snapshot_id=context["snapshot_id"],
                    save_command_id=cmd,
                    saved_by_staff_id=p.staff_id,
                    editor_schema_version="patient-editor.v1",
                    order_builder_version="order-builder.v1",
                    compilation_state="INCOMPLETE" if compiled["issues"] else "COMPLETE",
                    orders_hash=fingerprint(compiled["orders"]),
                    content_hash=revision_hash,
                    selection_manifest=manifest,
                    staff_snapshot={"operator_staff_id": str(p.staff_id)},
                    change_reason=request.change_reason,
                ),
            )
            for field, value in compiled["fields"].items():
                await insert(
                    c,
                    "clinical.patient_regimen_field_value",
                    dict(
                        created_by_principal=p.subject,
                        revision_id=revision["id"],
                        field_key=field,
                        value_json=value,
                        value_state="MISSING" if value in (None, "") else "PRESENT",
                        value_source="DOCTOR" if field in request.field_values else "SNAPSHOT",
                        provenance_ref={"snapshot_id": str(context["snapshot_id"])},
                    ),
                )
            for order in compiled["orders"]:
                await insert(
                    c,
                    "clinical.patient_regimen_order_item",
                    dict(
                        created_by_principal=p.subject,
                        revision_id=revision["id"],
                        order_item_key=uuid4(),
                        **order,
                        content_hash=fingerprint(order),
                    ),
                )
            await c.execute(
                "UPDATE clinical.patient_regimen_instance SET current_revision_id=$2,current_confirmed_revision_id=NULL WHERE id=$1",
                instance_id,
                revision["id"],
            )
            await insert(
                c,
                "clinical.doctor_action_event",
                dict(
                    created_by_principal=p.subject,
                    launch_context_id=context_id,
                    actor_staff_id=p.staff_id,
                    command_receipt_id=cmd,
                    instance_id=instance_id,
                    revision_id=revision["id"],
                    action_type="SAVE",
                    acted_at=datetime.now(UTC),
                    reason=request.change_reason,
                    change_set=[
                        {"field": k, "value_hash": fingerprint(v)}
                        for k, v in request.field_values.items()
                    ],
                ),
            )
            result = {
                "instance_id": str(instance_id),
                "revision_id": str(revision["id"]),
                "revision_hash": revision_hash,
                "row_version": row["row_version"] + 1,
                "issues": compiled["issues"],
                "revision_no": revision_no,
            }
            await self.audit(
                c,
                p,
                context,
                "PATIENT_REVISION_SAVED",
                revision["id"],
                {"revision_no": revision_no, "hash": revision_hash},
            )
            await self.complete(c, cmd, result, "patient_regimen_revision", revision["id"])
            return result

    async def confirm(self, p, context_id, instance_id, request: ConfirmInput, key):
        async with self.pool.acquire() as c, c.transaction():
            context, row = await self.instance(c, context_id, p, instance_id, lock=True)
            cmd, cached = await self.command(
                c,
                p,
                "CONFIRM",
                key,
                {
                    "context_id": str(context_id),
                    "instance_id": str(instance_id),
                    **request.model_dump(mode="json"),
                },
            )
            if cached:
                return cached
            if (
                row["row_version"] != request.expected_row_version
                or row["current_revision_id"] != request.revision_id
            ):
                raise BusinessError("REVISION_CONFLICT", "确认的修订已变化，请重新核对", 409)
            rev = await c.fetchrow(
                "SELECT * FROM clinical.patient_regimen_revision WHERE id=$1", request.revision_id
            )
            if not rev or rev["content_hash"] != request.revision_hash:
                raise BusinessError("REVISION_HASH_MISMATCH", "确认内容与保存内容不一致", 409)
            if rev["snapshot_id"] != context["snapshot_id"]:
                raise BusinessError("SNAPSHOT_CHANGED", "患者数据已刷新，请重新核对并保存", 409)
            issues = rev["selection_manifest"].get("issues", [])
            required = {i["code"] for i in issues}
            if not required.issubset(set(request.acknowledged_codes)):
                raise BusinessError(
                    "ISSUES_NOT_ACKNOWLEDGED", "请逐项核对本次方案的待处理事项", 409
                )
            if p.usage_mode != "TEST_ONLY" or row["availability_state"] != "TEST_ONLY":
                raise BusinessError(
                    "CLINICAL_RELEASE_NOT_CONFIGURED", "临床放行与真实院方身份尚未配置", 503
                )
            event = await insert(
                c,
                "clinical.doctor_action_event",
                dict(
                    created_by_principal=p.subject,
                    launch_context_id=context_id,
                    actor_staff_id=p.staff_id,
                    command_receipt_id=cmd,
                    instance_id=instance_id,
                    revision_id=rev["id"],
                    action_type="CONFIRM",
                    acted_at=datetime.now(UTC),
                    confirmed_content_hash=rev["content_hash"],
                    change_set=[{"acknowledged_code": code} for code in sorted(required)],
                ),
            )
            await c.execute(
                "UPDATE clinical.patient_regimen_instance SET current_confirmed_revision_id=$2 WHERE id=$1",
                instance_id,
                rev["id"],
            )
            result = {
                "confirmation_id": str(event["id"]),
                "revision_id": str(rev["id"]),
                "row_version": row["row_version"] + 1,
                "mode": "TEST_ONLY",
                "hospital_status": "NOT_SUBMITTED",
                "reviewer_status": "NOT_CONFIGURED",
            }
            await self.audit(
                c,
                p,
                context,
                "TEST_REVISION_CONFIRMED",
                rev["id"],
                {"hash": rev["content_hash"], "hospital": "NOT_SUBMITTED"},
            )
            await self.complete(c, cmd, result, "doctor_action_event", event["id"])
            return result
