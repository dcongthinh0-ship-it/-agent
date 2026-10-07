from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from chemo_agent_product.agent_adapter import (
    TOOLS,
    ClaudeAdapter,
    prompt_path,
    sdk_version,
    validate_output,
)
from chemo_agent_product.database import insert
from chemo_agent_product.domain import Reference, fingerprint
from chemo_agent_product.patient_contracts import AgentRequest
from chemo_agent_product.security import BusinessError


class AgentService:
    def __init__(self, workflow, adapter=None):
        self.workflow = workflow
        self.pool = workflow.pool
        self.settings = workflow.settings
        self.adapter = adapter or ClaudeAdapter(self.settings)

    def profile_payload(self, kind):
        return {
            "kind": kind,
            "model": (self.settings.reviewer_model_name or self.settings.model_name)
            if kind == "REVIEWER"
            else self.settings.model_name,
            "prompt_hash": fingerprint(prompt_path(kind).read_text()),
            "tools": list(TOOLS),
            "max_turns": self.settings.model_max_turns,
            "max_budget_usd": self.settings.model_max_budget_usd,
            "timeout": self.settings.model_timeout_seconds,
            "schema": "agent-output.v1",
            "sdk_version": sdk_version(),
        }

    async def enqueue(self, p, context_id, request: AgentRequest, key):
        async with self.pool.acquire() as c, c.transaction():
            return await self.enqueue_at(c, p, context_id, request, key)

    async def enqueue_at(self, c, p, context_id, request: AgentRequest, key):
        context = await self.workflow.scoped_context(c, context_id, p)
        cmd, cached = await self.workflow.command(
            c,
            p,
            "AGENT_RUN",
            key,
            {"context_id": str(context_id), **request.model_dump(mode="json")},
        )
        if cached:
            return cached
        decision = await c.fetchrow(
            """SELECT * FROM clinical.decision_run WHERE launch_context_id=$1
          AND prepare_run_id=$2 AND purpose='MATCH_CANDIDATES' AND status='SUCCEEDED'
          ORDER BY created_at DESC LIMIT 1""",
            context_id,
            context["current_prepare_run_id"],
        )
        if not decision:
            raise BusinessError("PREPARE_NOT_READY", "请等待当前患者准备完成", 409)
        revision = None
        if request.kind == "REVIEWER":
            if not request.revision_id:
                raise BusinessError("REVIEW_REVISION_REQUIRED", "请先保存需要复核的修订", 422)
            revision = await c.fetchrow(
                """SELECT r.*,i.template_ref_id FROM clinical.patient_regimen_revision r
              JOIN clinical.patient_regimen_instance i ON i.id=r.instance_id WHERE r.id=$1 AND i.origin_context_id=$2""",
                request.revision_id,
                context_id,
            )
            if not revision:
                raise BusinessError("REVISION_FORBIDDEN", "复核修订不属于当前患者", 403)
            manifest = revision["selection_manifest"].get("knowledge_manifest", {})
            decision = await insert(
                c,
                "clinical.decision_run",
                dict(
                    created_by_principal=p.subject,
                    launch_context_id=context_id,
                    snapshot_id=revision["snapshot_id"],
                    purpose="ASSESS_REVISION",
                    selected_template_ref_id=revision["template_ref_id"],
                    target_revision_id=revision["id"],
                    input_hash=revision["content_hash"],
                    input_contract_version="revision.v1",
                    capability_manifest={"reviewer": "READ_ONLY"},
                    orchestrator_version="patient-flow.v1",
                    usage_mode="TEST_ONLY",
                    knowledge_manifest=manifest,
                    manifest_hash=fingerprint(manifest),
                    status="SUCCEEDED",
                    release_gate_state="UNVERIFIED",
                    outcome_code="REVIEW_REQUESTED",
                    output_contract_version="review-input.v1",
                    output_payload={"revision_id": str(revision["id"])},
                    output_hash=fingerprint({"revision_id": str(revision["id"])}),
                ),
            )
        elif request.revision_id:
            raise BusinessError("AGENT_REVISION_NOT_EXPECTED", "主 Agent 任务不接受复核修订", 422)
        profile_payload = self.profile_payload(request.kind)
        profile_hash = fingerprint(profile_payload)
        await c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"profile:{request.kind}"
        )
        profile = await c.fetchrow(
            "SELECT * FROM agent.agent_profile_version WHERE config_hash=$1 AND profile_kind=$2",
            profile_hash,
            request.kind,
        )
        if not profile:
            version = await c.fetchval(
                "SELECT coalesce(max(version_no),0)+1 FROM agent.agent_profile_version WHERE profile_key=$1",
                f"clinical-{request.kind.lower()}",
            )
            profile = await insert(
                c,
                "agent.agent_profile_version",
                dict(
                    created_by_principal=p.subject,
                    profile_key=f"clinical-{request.kind.lower()}",
                    profile_kind=request.kind,
                    version_no=version,
                    runtime_kind="CLAUDE_AGENT_SDK",
                    model_ref=profile_payload["model"] or "NOT_CONFIGURED",
                    system_prompt_ref=prompt_path(request.kind).name,
                    system_prompt_hash=profile_payload["prompt_hash"],
                    toolset_policy={"allowed": list(TOOLS), "builtins": []},
                    permission_policy={"access": "READ_ONLY", "scope": "BOUND_INPUT"},
                    budget_policy={
                        k: profile_payload[k] for k in ["max_turns", "max_budget_usd", "timeout"]
                    },
                    output_schema_version="agent-output.v1",
                    config_hash=profile_hash,
                    status="DRAFT",
                ),
            )
        snapshot = await c.fetchrow(
            "SELECT * FROM clinical.clinical_snapshot WHERE id=$1", decision["snapshot_id"]
        )
        reference = {
            "context_id": str(context_id),
            "decision_id": str(decision["id"]),
            "matching_decision_id": str(decision["id"])
            if request.kind == "RECOMMENDATION"
            else str(
                await c.fetchval(
                    """SELECT id FROM clinical.decision_run WHERE launch_context_id=$1
          AND purpose='MATCH_CANDIDATES' AND snapshot_id=$2 ORDER BY created_at DESC LIMIT 1""",
                    context_id,
                    revision["snapshot_id"],
                )
            ),
            "snapshot_ref": Reference(
                namespace="clinical.clinical_snapshot",
                id=str(snapshot["id"]),
                version=str(snapshot["snapshot_no"]),
                content_hash=snapshot["content_hash"],
            ).model_dump(mode="json"),
            "question": request.question,
            "revision_id": str(revision["id"]) if revision else None,
            "revision_hash": revision["content_hash"] if revision else None,
            "knowledge_manifest": decision["knowledge_manifest"],
        }
        run = await insert(
            c,
            "agent.agent_run",
            dict(
                created_by_principal=p.subject,
                decision_run_id=decision["id"],
                profile_version_id=profile["id"],
                status="QUEUED" if self.settings.model_configured else "FAILED",
                input_contract_version="agent-input.v1",
                input_reference=reference,
                input_hash=fingerprint(reference),
                sdk_runtime_version=sdk_version(),
                trace_id=str(uuid4()),
                error_code=None if self.settings.model_configured else "MODEL_NOT_CONFIGURED",
                completed_at=None if self.settings.model_configured else datetime.now(UTC),
            ),
        )
        if self.settings.model_configured:
            await insert(
                c,
                "ops.job",
                dict(
                    created_by_principal=p.subject,
                    hospital_id=p.hospital_id,
                    job_kind="AGENT_RUN",
                    agent_run_id=run["id"],
                    dedupe_key=f"agent:{run['id']}",
                    available_at=await c.fetchval("SELECT clock_timestamp()"),
                    max_attempts=1,
                ),
            )
        result = {
            "agent_run_id": str(run["id"]),
            "status": run["status"],
            "error_code": run["error_code"],
            "runtime_kind": "CLAUDE_AGENT_SDK",
        }
        await self.workflow.audit(
            c,
            p,
            context,
            "AGENT_REQUESTED",
            run["id"],
            {"kind": request.kind, "status": run["status"]},
        )
        await self.workflow.complete(c, cmd, result, "agent_run", run["id"])
        return result

    async def read(self, p, context_id, run_id):
        async with self.pool.acquire() as c, c.transaction(readonly=True):
            await self.workflow.scoped_context(c, context_id, p, active=False)
            run = await c.fetchrow(
                """SELECT a.*,p.profile_kind FROM agent.agent_run a
              JOIN agent.agent_profile_version p ON p.id=a.profile_version_id JOIN clinical.decision_run d ON d.id=a.decision_run_id
              WHERE a.id=$1 AND d.launch_context_id=$2""",
                run_id,
                context_id,
            )
            if not run:
                raise BusinessError("AGENT_RUN_FORBIDDEN", "智能体记录不属于当前患者", 403)
            outputs = await c.fetch(
                "SELECT output_kind,validation_state,structured_payload FROM agent.agent_output WHERE agent_run_id=$1",
                run_id,
            )
            calls = await c.fetch(
                "SELECT call_no,tool_name,status,error_code,started_at,completed_at FROM agent.agent_tool_call WHERE agent_run_id=$1 ORDER BY call_no",
                run_id,
            )
        return {
            "agent_run_id": str(run_id),
            "kind": run["profile_kind"],
            "status": run["status"],
            "error_code": run["error_code"],
            "revision_id": run["input_reference"].get("revision_id"),
            "snapshot_id": run["input_reference"]["snapshot_ref"]["id"],
            "tools": [dict(r) for r in calls],
            "outputs": [dict(r) for r in outputs],
            "usage": run["usage_summary"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
        }

    async def list_runs(self, p, context_id, kind, revision_id=None):
        async with self.pool.acquire() as c, c.transaction(readonly=True):
            context = await self.workflow.scoped_context(c, context_id, p, active=False)
            rows = await c.fetch(
                """SELECT a.id,a.status,a.error_code,a.created_at
              FROM agent.agent_run a JOIN agent.agent_profile_version ap ON ap.id=a.profile_version_id
              JOIN clinical.decision_run d ON d.id=a.decision_run_id
              WHERE d.launch_context_id=$1 AND ap.profile_kind=$2
              AND (($3::uuid IS NULL AND d.snapshot_id=$4) OR d.target_revision_id=$3)
              ORDER BY a.created_at DESC,a.id DESC LIMIT 10""",
                context_id,
                kind,
                revision_id,
                context["snapshot_id"],
            )
        return {"items": [dict(row) for row in rows]}

    async def run_job(self, job, worker):
        async with self.pool.acquire() as c, c.transaction():
            await worker.fenced(c, job)
            run = await c.fetchrow(
                """SELECT a.*,p.profile_kind,p.config_hash FROM agent.agent_run a
              JOIN agent.agent_profile_version p ON p.id=a.profile_version_id WHERE a.id=$1 FOR UPDATE OF a""",
                job["agent_run_id"],
            )
            await c.execute(
                "UPDATE agent.agent_run SET status='RUNNING',started_at=now() WHERE id=$1",
                run["id"],
            )
        try:
            if fingerprint(self.profile_payload(run["profile_kind"])) != run["config_hash"]:
                raise BusinessError(
                    "AGENT_PROFILE_CHANGED", "智能体配置已变更，请重新请求当前任务", 409
                )
            bindings = await self.bindings(run)

            async def dispatch(name, args):
                return await self.call(run, bindings, name, args, job, worker)

            payload, usage = await self.adapter.run(
                run["profile_kind"], run["input_reference"], dispatch
            )
            output = validate_output(
                payload,
                run["profile_kind"],
                bindings["refs"],
                bindings["snapshot"]["text_records"],
                set(bindings["candidates"]),
            )
            async with self.pool.acquire() as c, c.transaction():
                await worker.fenced(c, job)
                await self.assert_active(c, run)
                await insert(
                    c,
                    "agent.agent_output",
                    dict(
                        created_by_principal=run["created_by_principal"],
                        agent_run_id=run["id"],
                        output_kind=run["profile_kind"],
                        schema_version="agent-output.v1",
                        validation_state="VALID",
                        structured_payload=output.model_dump(mode="json"),
                        evidence_refs=[r.model_dump(mode="json") for r in bindings["refs"]],
                        output_hash=fingerprint(output),
                    ),
                )
                await c.execute(
                    "UPDATE agent.agent_run SET status='SUCCEEDED',completed_at=now(),usage_summary=$2 WHERE id=$1",
                    run["id"],
                    usage,
                )
                await c.execute(
                    "UPDATE ops.job SET status='SUCCEEDED',lease_expires_at=NULL WHERE id=$1",
                    job["id"],
                )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "MODEL_TIMEOUT"
                if isinstance(exc, TimeoutError)
                else "MODEL_RUN_FAILED"
            )
            async with self.pool.acquire() as c, c.transaction():
                await worker.fenced(c, job)
                await c.execute(
                    "UPDATE agent.agent_run SET status=$2,error_code=$3,completed_at=now() WHERE id=$1",
                    run["id"],
                    "TIMEOUT" if code == "MODEL_TIMEOUT" else "FAILED",
                    code,
                )
                await c.execute(
                    "UPDATE ops.job SET status='FAILED',last_error_code=$2,lease_expires_at=NULL WHERE id=$1",
                    job["id"],
                    code,
                )

    async def assert_active(self, c, run):
        row = await c.fetchrow(
            """SELECT lc.context_state,lc.expires_at,lc.current_prepare_run_id,d.prepare_run_id
          FROM clinical.decision_run d JOIN clinical.launch_context lc ON lc.id=d.launch_context_id WHERE d.id=$1""",
            run["decision_run_id"],
        )
        if (
            row["context_state"] != "ACTIVE"
            or row["expires_at"] <= datetime.now(UTC)
            or (row["prepare_run_id"] and row["prepare_run_id"] != row["current_prepare_run_id"])
        ):
            raise BusinessError(
                "AGENT_CONTEXT_STALE", "患者上下文已经变化，本次输出不再覆盖当前页面", 409
            )

    async def bindings(self, run):
        ref = run["input_reference"]
        refs = [Reference.model_validate(ref["snapshot_ref"])]
        async with self.pool.acquire() as c, c.transaction(readonly=True):
            await self.assert_active(c, run)
            snapshot = await c.fetchval(
                "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1",
                UUID(ref["snapshot_ref"]["id"]),
            )
            for fact in snapshot["facts"].values():
                refs.append(Reference.model_validate(fact["source"]))
            for record in snapshot["text_records"]:
                refs.append(Reference.model_validate(record["source"]))
            candidates = await c.fetch(
                """SELECT dc.*,t.template_payload,t.content_hash AS template_hash,t.id AS projection_id
              FROM clinical.decision_candidate dc JOIN catalog_bridge.template_version_reference t ON t.id=dc.template_ref_id
              WHERE dc.decision_run_id=$1 ORDER BY presentation_region,evidence_level NULLS LAST,candidate_key""",
                UUID(ref["matching_decision_id"])
                if ref["matching_decision_id"] != "None"
                else run["decision_run_id"],
            )
            for candidate in candidates:
                refs.append(
                    Reference(
                        namespace="catalog_bridge.template_version_reference",
                        id=str(candidate["projection_id"]),
                        version="template-projection.v1",
                        content_hash=candidate["template_hash"],
                    )
                )
            manifest = ref["knowledge_manifest"]
            for group in ["evidence", "applicability", "rule_packages"]:
                refs.extend(Reference.model_validate(r) for r in manifest.get(group, []))
            evidence = {}
            for r in manifest.get("evidence", []):
                row = await c.fetchrow(
                    "SELECT * FROM knowledge.evidence_record_version WHERE id=$1", UUID(r["id"])
                )
                if not row or row["content_hash"] != r["content_hash"]:
                    raise BusinessError(
                        "KNOWLEDGE_VERSION_CHANGED", "本次证据固定版本发生变化", 409
                    )
                evidence[r["id"]] = {**dict(row), "ref": r}
            revision = (
                await c.fetchrow(
                    "SELECT * FROM clinical.patient_regimen_revision WHERE id=$1",
                    UUID(ref["revision_id"]),
                )
                if ref["revision_id"]
                else None
            )
            if revision:
                if revision["content_hash"] != ref["revision_hash"]:
                    raise BusinessError("REVISION_HASH_MISMATCH", "复核修订内容不一致", 409)
                refs.append(
                    Reference(
                        namespace="clinical.patient_regimen_revision",
                        id=str(revision["id"]),
                        version=str(revision["revision_no"]),
                        content_hash=revision["content_hash"],
                    )
                )
                orders = await c.fetch(
                    "SELECT * FROM clinical.patient_regimen_order_item WHERE revision_id=$1 ORDER BY line_no",
                    revision["id"],
                )
                fields = await c.fetch(
                    "SELECT field_key,value_json,value_state,value_source,provenance_ref "
                    "FROM clinical.patient_regimen_field_value WHERE revision_id=$1 ORDER BY field_key",
                    revision["id"],
                )
            else:
                orders = []
                fields = []
        return {
            "snapshot": snapshot,
            "refs": refs,
            "candidates": {r["id"]: dict(r) for r in candidates},
            "evidence": evidence,
            "manifest": manifest,
            "revision": dict(revision) if revision else None,
            "orders": [dict(r) for r in orders],
            "field_values": {r["field_key"]: r["value_json"] for r in fields},
            "field_provenance": {
                r["field_key"]: {k: r[k] for k in ("value_state", "value_source", "provenance_ref")}
                for r in fields
            },
        }

    async def call(self, run, b, name, args, job, worker):
        async with self.pool.acquire() as c, c.transaction():
            await worker.fenced(c, job)
            await self.assert_active(c, run)
            await c.execute("SELECT id FROM agent.agent_run WHERE id=$1 FOR UPDATE", run["id"])
            number = await c.fetchval(
                "SELECT coalesce(max(call_no),0)+1 FROM agent.agent_tool_call WHERE agent_run_id=$1",
                run["id"],
            )
            safe = {k: v for k, v in args.items() if k in {"candidate_id", "evidence_id"}}
            if "query" in args:
                safe.update(
                    query_hash=fingerprint(args["query"]), query_length=len(str(args["query"]))
                )
            call = await insert(
                c,
                "agent.agent_tool_call",
                dict(
                    created_by_principal=run["created_by_principal"],
                    agent_run_id=run["id"],
                    call_no=number,
                    tool_name=name,
                    tool_category="CLINICAL_READ",
                    request_schema_version="clinical-tools.v1",
                    request_safe_args=safe,
                    request_hash=fingerprint(args),
                    status="STARTED",
                    started_at=datetime.now(UTC),
                ),
            )
        try:
            if name not in TOOLS:
                raise BusinessError("TOOL_DENIED", "该工具未授权", 403)
            expected = (
                {"candidate_id"}
                if name == "read_plan"
                else {"evidence_id"}
                if name == "read_evidence"
                else {"query"}
                if name == "search_knowledge"
                else set()
            )
            if set(args) != expected:
                raise BusinessError("TOOL_ARGUMENT_INVALID", "工具输入不符合合同", 422)
            if name == "read_snapshot":
                result = {"snapshot": b["snapshot"], "ref": run["input_reference"]["snapshot_ref"]}
            elif name == "read_candidates":
                result = {
                    "items": [
                        {k: v for k, v in r.items() if k != "template_payload"}
                        for r in b["candidates"].values()
                    ]
                }
            elif name == "read_plan":
                row = b["candidates"].get(UUID(args["candidate_id"]))
                if not row:
                    raise BusinessError("TOOL_SCOPE_DENIED", "方案不属于本次候选", 403)
                result = row
            elif name == "read_evidence":
                result = b["evidence"].get(str(UUID(args["evidence_id"])))
                if not result:
                    raise BusinessError("TOOL_SCOPE_DENIED", "证据不属于本次资料", 403)
            elif name == "read_rules":
                result = {
                    "manifest": b["manifest"],
                    "candidates": [
                        {k: r[k] for k in ["id", "data_labels", "safety_labels", "x_basis"]}
                        for r in b["candidates"].values()
                    ],
                }
            elif name == "read_revision":
                result = {
                    "revision": b["revision"],
                    "orders": b["orders"],
                    "field_values": b["field_values"],
                    "field_provenance": b["field_provenance"],
                }
            elif name == "read_calculations":
                if b["revision"]:
                    saved = b["revision"]["selection_manifest"]
                    result = {
                        "state": "SAVED_REVISION",
                        "bsa": saved.get("bsa"),
                        "reference_doses": saved.get("reference_doses", {}),
                        "revision_id": str(b["revision"]["id"]),
                    }
                else:
                    from chemo_agent_product.domain import PatientSnapshot
                    from chemo_agent_product.editor import compile_revision
                    from chemo_agent_product.patient_contracts import SaveInput

                    snapshot = PatientSnapshot.model_validate(b["snapshot"])
                    items = []
                    for candidate in b["candidates"].values():
                        template = candidate["template_payload"]
                        calculated = compile_revision(
                            template,
                            snapshot,
                            SaveInput(expected_row_version=1),
                            self.workflow.initial_fields(template, snapshot),
                            b["manifest"].get("calculation", {}),
                        )
                        items.append(
                            {
                                "candidate_id": str(candidate["id"]),
                                "bsa": calculated["manifest"]["bsa"],
                                "reference_doses": calculated["manifest"]["reference_doses"],
                            }
                        )
                    result = {"state": "CONTROLLED_REFERENCE_ONLY", "items": items}
            else:
                query = str(args["query"]).strip()
                if not query or len(query) > 200:
                    raise BusinessError("TOOL_ARGUMENT_INVALID", "检索文字长度无效", 422)
                result = {
                    "items": [
                        e
                        for e in b["evidence"].values()
                        if query.casefold() in e["verbatim_excerpt"].casefold()
                    ][:20]
                }
            async with self.pool.acquire() as c, c.transaction():
                await worker.fenced(c, job)
                await self.assert_active(c, run)
                await c.execute(
                    "UPDATE agent.agent_tool_call SET status='SUCCEEDED',result_reference=$2,result_hash=$3,completed_at=now() WHERE id=$1",
                    call["id"],
                    {"contract": "clinical-tools.v1", "tool": name},
                    fingerprint(result),
                )
            return result
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "TOOL_ARGUMENT_INVALID"
                if isinstance(exc, (ValueError, TypeError))
                else "TOOL_READ_FAILED"
            )
            async with self.pool.acquire() as c:
                await c.execute(
                    "UPDATE agent.agent_tool_call SET status=$3,error_code=$2,completed_at=now() WHERE id=$1",
                    call["id"],
                    code,
                    "FAILED" if code == "TOOL_READ_FAILED" else "DENIED",
                )
            raise BusinessError(code, "工具请求不符合本次患者范围或输入合同", 403) from None
