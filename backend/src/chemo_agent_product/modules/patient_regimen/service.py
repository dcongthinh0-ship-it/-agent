from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from chemo_agent_product.agent_runtime.contracts import AgentRequest
from chemo_agent_product.core.domain import PatientSnapshot, fingerprint
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.modules.patient_regimen import repository as repository
from chemo_agent_product.modules.patient_regimen.schemas import ConfirmInput, SaveInput

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


class PatientRegimenService:
    def __init__(self, workflow):
        self.workflow = workflow

    async def select(self, p, context_id, candidate_id, key):
        async with self.workflow.pool.acquire() as c, c.transaction():
            context, row = await self.workflow.candidate(c, context_id, p, candidate_id)
            cmd, cached = await self.workflow.command(
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
            instance = await repository.insert_patient_regimen_instance(
                c,
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
            await repository.insert_doctor_action_event(
                c,
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
            await self.workflow.audit(c, p, context, "CANDIDATE_SELECTED", instance["id"], result)
            await self.workflow.complete(c, cmd, result, "patient_regimen_instance", instance["id"])
            return result

    async def instance(self, c, context_id, p, instance_id, lock=False):
        context = await self.workflow.scoped_context(c, context_id, p)
        row = await repository.get_scoped_instance(
            c,
            instance_id,
            p.hospital_id,
            context["patient_reference_id"],
            context["encounter_reference_id"],
            lock=lock,
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

    async def read_instance(self, p, context_id, instance_id, revision_id=None):
        async with self.workflow.pool.acquire() as c, c.transaction(readonly=True):
            context, row = await self.workflow.instance(c, context_id, p, instance_id)
            if revision_id and not await repository.find_instance_revision(
                c, revision_id, instance_id
            ):
                raise BusinessError("REVISION_FORBIDDEN", "该修订不属于本次患者方案", 403)
            chosen_revision = revision_id or row["current_revision_id"]
            rev = await repository.get_revision(c, chosen_revision) if chosen_revision else None
            snapshot_id = rev["snapshot_id"] if rev else row["origin_snapshot_id"]
            payload = await repository.get_snapshot_payload(c, snapshot_id)
            if not payload:
                raise BusinessError("SNAPSHOT_NOT_READY", "患者数据尚未准备完成", 409)
            values = await repository.list_revision_fields(c, rev["id"]) if rev else []
            history = await repository.list_revision_history(c, instance_id)
        return dict(
            instance_id=str(instance_id),
            read_only=bool(
                row["origin_context_id"] != context_id
                or row["origin_snapshot_id"] != context["snapshot_id"]
                or revision_id
                and revision_id != row["current_revision_id"]
            ),
            row_version=row["row_version"],
            template=row["template_payload"],
            snapshot=payload,
            field_values={r["field_key"]: r["value_json"] for r in values}
            if rev
            else self.workflow.initial_fields(
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
            issues=rev["selection_manifest"].get("issues", []) if rev else [],
            calculations=rev["selection_manifest"].get("reference_doses", {}) if rev else {},
            bsa=rev["selection_manifest"].get("bsa") if rev else None,
        )

    async def save(self, p, context_id, instance_id, request: SaveInput, key):
        from chemo_agent_product.modules.patient_regimen.compiler import compile_revision

        async with self.workflow.pool.acquire() as c, c.transaction():
            context, row = await self.workflow.instance(c, context_id, p, instance_id, lock=True)
            cmd, cached = await self.workflow.command(
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
            if request.base_revision_id and not (request.change_reason or "").strip():
                raise BusinessError("CHANGE_REASON_REQUIRED", "修改已保存方案时请填写修改原因", 422)
            previous = (
                await repository.get_revision(c, request.base_revision_id)
                if request.base_revision_id
                else None
            )
            snapshot_id = previous["snapshot_id"] if previous else row["origin_snapshot_id"]
            if snapshot_id != context["snapshot_id"]:
                raise BusinessError(
                    "SNAPSHOT_CHANGED",
                    "患者数据已更新，请从当前候选重新选用；历史方案保持原快照",
                    409,
                )
            snapshot_payload = await repository.get_snapshot_payload(c, snapshot_id)
            if not snapshot_payload:
                raise BusinessError("SNAPSHOT_NOT_READY", "患者数据尚未准备完成", 409)
            snapshot = PatientSnapshot.model_validate(snapshot_payload)
            inherited_fields = {}
            inherited_meds = {}
            if previous:
                allowed = {
                    f["field_key"]
                    for f in row["template_payload"]["fields"]
                    if f["edit_policy"] == "RUNTIME_EDITABLE"
                }
                inherited_fields = {
                    v["field_key"]: v["value_json"]
                    for v in await repository.list_revision_fields(c, previous["id"])
                    if v["field_key"] in allowed
                }
                inherited_meds = previous["selection_manifest"].get("medication_values", {})
            merged = SaveInput.model_validate(
                {
                    **request.model_dump(mode="json"),
                    "field_values": {**inherited_fields, **request.field_values},
                    "medication_values": {
                        **inherited_meds,
                        **request.model_dump(mode="json")["medication_values"],
                    },
                }
            )
            compiled = compile_revision(
                row["template_payload"],
                snapshot,
                merged,
                self.workflow.initial_fields(row["template_payload"], snapshot),
                (row["knowledge_manifest"] or {}).get("calculation", {}),
            )
            revision_no = await repository.next_revision_number(c, instance_id)
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
            revision = await repository.insert_patient_regimen_revision(
                c,
                dict(
                    created_by_principal=p.subject,
                    instance_id=instance_id,
                    revision_no=revision_no,
                    base_revision_id=request.base_revision_id,
                    snapshot_id=snapshot_id,
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
                await repository.insert_patient_regimen_field_value(
                    c,
                    dict(
                        created_by_principal=p.subject,
                        revision_id=revision["id"],
                        field_key=field,
                        value_json=value,
                        value_state="MISSING" if value in (None, "") else "PRESENT",
                        value_source="DOCTOR" if field in merged.field_values else "SNAPSHOT",
                        provenance_ref={"snapshot_id": str(snapshot_id)},
                    ),
                )
            for order in compiled["orders"]:
                await repository.insert_patient_regimen_order_item(
                    c,
                    dict(
                        created_by_principal=p.subject,
                        revision_id=revision["id"],
                        order_item_key=uuid4(),
                        **order,
                        content_hash=fingerprint(order),
                    ),
                )
            await repository.set_current_revision(c, instance_id, revision["id"])
            await repository.insert_doctor_action_event(
                c,
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
            await self.workflow.audit(
                c,
                p,
                context,
                "PATIENT_REVISION_SAVED",
                revision["id"],
                {"revision_no": revision_no, "hash": revision_hash},
            )
            await self.workflow.complete(c, cmd, result, "patient_regimen_revision", revision["id"])
            return result

    async def confirm(self, p, context_id, instance_id, request: ConfirmInput, key):
        async with self.workflow.pool.acquire() as c, c.transaction():
            context, row = await self.workflow.instance(c, context_id, p, instance_id, lock=True)
            cmd, cached = await self.workflow.command(
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
            rev = await repository.get_revision(c, request.revision_id)
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
            event = await repository.insert_doctor_action_event(
                c,
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
            await repository.set_confirmed_revision(c, instance_id, rev["id"])
            result = {
                "confirmation_id": str(event["id"]),
                "revision_id": str(rev["id"]),
                "row_version": row["row_version"] + 1,
                "mode": "TEST_ONLY",
                "hospital_status": "NOT_SUBMITTED",
                "reviewer_status": "NOT_CONFIGURED",
            }
            await self.workflow.audit(
                c,
                p,
                context,
                "TEST_REVISION_CONFIRMED",
                rev["id"],
                {"hash": rev["content_hash"], "hospital": "NOT_SUBMITTED"},
            )
            await self.workflow.complete(c, cmd, result, "doctor_action_event", event["id"])
            return result

    async def confirm_with_review(self, p, context_id, instance_id, request, idempotency_key):
        result = await self.workflow.confirm(p, context_id, instance_id, request, idempotency_key)
        from chemo_agent_product.agent_runtime.service import AgentService

        try:
            review = await AgentService(self.workflow).enqueue(
                p,
                context_id,
                AgentRequest(kind="REVIEWER", revision_id=request.revision_id),
                f"auto-review:{result['confirmation_id']}",
            )
            return {
                **result,
                "reviewer_status": review["status"],
                "reviewer_run_id": review["agent_run_id"],
            }
        except BusinessError as exc:
            return {**result, "reviewer_status": "REQUEST_FAILED", "reviewer_error_code": exc.code}
