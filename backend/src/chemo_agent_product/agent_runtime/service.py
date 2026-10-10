from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from chemo_agent_product.agent_runtime.contracts import AgentRequest
from chemo_agent_product.agent_runtime.policy import TOOLS, prompt_path, validate_output
from chemo_agent_product.core.domain import Reference, fingerprint
from chemo_agent_product.core.observability import emit
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.integrations.model.claude import ClaudeAdapter, sdk_version

from . import repository
from .bindings import BindingLoader
from .tools.service import ClinicalToolService

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, workflow, adapter=None):
        self.workflow = workflow
        self.pool = workflow.pool
        self.settings = workflow.settings
        self.adapter = adapter or ClaudeAdapter(self.settings)
        self.binding_loader = BindingLoader(self)
        self.tools = ClinicalToolService(self)

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
        decision = await repository.get_prepared_decision(
            c, context_id, context["current_prepare_run_id"]
        )
        if not decision:
            raise BusinessError("PREPARE_NOT_READY", "请等待当前患者准备完成", 409)
        revision = None
        if request.kind == "REVIEWER":
            if not request.revision_id:
                raise BusinessError("REVIEW_REVISION_REQUIRED", "请先保存需要复核的修订", 422)
            revision = await repository.get_scoped_review_revision(
                c, request.revision_id, context_id
            )
            if not revision:
                raise BusinessError("REVISION_FORBIDDEN", "复核修订不属于当前患者", 403)
            manifest = revision["selection_manifest"].get("knowledge_manifest", {})
            decision = await repository.insert_decision_run(
                c,
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
        await repository.lock_profile(c, f"profile:{request.kind}")
        profile = await repository.find_profile(c, profile_hash, request.kind)
        if not profile:
            version = await repository.next_profile_version(c, f"clinical-{request.kind.lower()}")
            profile = await repository.insert_agent_profile_version(
                c,
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
        snapshot = await repository.get_snapshot(c, decision["snapshot_id"])
        reference = {
            "context_id": str(context_id),
            "decision_id": str(decision["id"]),
            "matching_decision_id": str(decision["id"])
            if request.kind == "RECOMMENDATION"
            else str(
                await repository.find_snapshot_decision(c, context_id, revision["snapshot_id"])
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
        run = await repository.insert_agent_run(
            c,
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
            await repository.insert_job(
                c,
                dict(
                    created_by_principal=p.subject,
                    hospital_id=p.hospital_id,
                    job_kind="AGENT_RUN",
                    agent_run_id=run["id"],
                    dedupe_key=f"agent:{run['id']}",
                    available_at=await repository.database_time(c),
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
            run = await repository.get_scoped_run(c, run_id, context_id)
            if not run:
                raise BusinessError("AGENT_RUN_FORBIDDEN", "智能体记录不属于当前患者", 403)
            outputs = await repository.list_outputs(c, run_id)
            calls = await repository.list_tool_calls(c, run_id)
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
            rows = await repository.list_scoped_runs(
                c, context_id, kind, revision_id, context["snapshot_id"]
            )
        return {"items": [dict(row) for row in rows]}

    async def run_job(self, job, worker):
        started = perf_counter()
        async with self.pool.acquire() as c, c.transaction():
            await worker.fenced(c, job)
            run = await repository.get_locked_run(c, job["agent_run_id"])
            await repository.start_run(c, run["id"])
        emit(logger, "agent_started", agent_run_id=run["id"], profile_kind=run["profile_kind"])
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
                await repository.insert_agent_output(
                    c,
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
                await repository.complete_run(c, run["id"], usage)
                await repository.complete_job(c, job["id"])
            emit(
                logger,
                "agent_completed",
                agent_run_id=run["id"],
                profile_kind=run["profile_kind"],
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "MODEL_TIMEOUT"
                if isinstance(exc, TimeoutError)
                else "MODEL_RUN_FAILED"
            )
            emit(
                logger,
                "agent_failed",
                level=logging.ERROR,
                exc=exc,
                error_code=code,
                agent_run_id=run["id"],
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            async with self.pool.acquire() as c, c.transaction():
                await worker.fenced(c, job)
                await repository.fail_run(
                    c, run["id"], "TIMEOUT" if code == "MODEL_TIMEOUT" else "FAILED", code
                )
                await repository.fail_job(c, job["id"], code)

    async def assert_active(self, c, run):
        row = await repository.get_run_context(c, run["decision_run_id"])
        if (
            row["context_state"] != "ACTIVE"
            or row["expires_at"] <= datetime.now(UTC)
            or (row["prepare_run_id"] and row["prepare_run_id"] != row["current_prepare_run_id"])
        ):
            raise BusinessError(
                "AGENT_CONTEXT_STALE", "患者上下文已经变化，本次输出不再覆盖当前页面", 409
            )

    async def bindings(self, run):
        return await self.binding_loader.bindings(run)

    async def call(self, run, b, name, args, job, worker):
        return await self.tools.call(run, b, name, args, job, worker)
