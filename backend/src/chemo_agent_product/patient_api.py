from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from chemo_agent_product.patient_contracts import (
    ActionInput,
    AgentRequest,
    ConfirmInput,
    LaunchInput,
    RefreshInput,
    SaveInput,
)
from chemo_agent_product.security import BusinessError, Principal, verify_test_token
from chemo_agent_product.workflow import Workflow

router = APIRouter(prefix="/api/v1")


async def principal(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    settings = request.app.state.settings
    if not authorization or not authorization.startswith("Bearer "):
        raise BusinessError("AUTH_REQUIRED", "请从可信工作站入口进入当前患者", 401)
    return verify_test_token(
        authorization[7:],
        settings.launch_signing_key.get_secret_value() if settings.launch_signing_key else None,
        settings.environment,
    )


async def workflow(request: Request) -> Workflow:
    value = getattr(request.app.state, "workflow", None)
    if not value:
        raise BusinessError("WORKFLOW_NOT_CONFIGURED", "患者流程服务尚未配置", 503)
    return value


PrincipalDep = Annotated[Principal, Depends(principal)]
WorkflowDep = Annotated[Workflow, Depends(workflow)]
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.post("/launch-context")
async def launch(
    request: LaunchInput, p: PrincipalDep, w: WorkflowDep, idempotency_key: CommandKey
):
    return await w.launch(p, request, idempotency_key)


@router.post("/contexts/{context_id}/refresh")
async def refresh(
    context_id: UUID,
    request: RefreshInput,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    return await w.refresh(p, context_id, idempotency_key, request.expected_generation)


@router.get("/contexts/{context_id}/candidates/{candidate_id}")
async def detail(context_id: UUID, candidate_id: UUID, p: PrincipalDep, w: WorkflowDep):
    return await w.detail(p, context_id, candidate_id)


@router.get("/contexts/{context_id}/candidates/{candidate_id}/evidence")
async def evidence(context_id: UUID, candidate_id: UUID, p: PrincipalDep, w: WorkflowDep):
    return await w.candidate_evidence(p, context_id, candidate_id)


@router.post("/contexts/{context_id}/candidates/{candidate_id}/select")
async def select(
    context_id: UUID,
    candidate_id: UUID,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    return await w.select(p, context_id, candidate_id, idempotency_key)


@router.get("/contexts/{context_id}/instances/{instance_id}")
async def instance(context_id: UUID, instance_id: UUID, p: PrincipalDep, w: WorkflowDep):
    return await w.read_instance(p, context_id, instance_id)


@router.get("/contexts/{context_id}/instances/{instance_id}/revisions/{revision_id}")
async def historical_revision(
    context_id: UUID, instance_id: UUID, revision_id: UUID, p: PrincipalDep, w: WorkflowDep
):
    return await w.read_instance(p, context_id, instance_id, revision_id)


@router.post("/contexts/{context_id}/instances/{instance_id}/revisions")
async def save(
    context_id: UUID,
    instance_id: UUID,
    request: SaveInput,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    return await w.save(p, context_id, instance_id, request, idempotency_key)


@router.post("/contexts/{context_id}/instances/{instance_id}/confirm")
async def confirm(
    context_id: UUID,
    instance_id: UUID,
    request: ConfirmInput,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    result = await w.confirm(p, context_id, instance_id, request, idempotency_key)
    from chemo_agent_product.agents import AgentService

    try:
        review = await AgentService(w).enqueue(
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


@router.post("/contexts/{context_id}/agent-runs")
async def start_agent(
    context_id: UUID,
    request: AgentRequest,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    from chemo_agent_product.agents import AgentService

    return await AgentService(w).enqueue(p, context_id, request, idempotency_key)


@router.get("/contexts/{context_id}/agent-runs/{run_id}")
async def read_agent(context_id: UUID, run_id: UUID, p: PrincipalDep, w: WorkflowDep):
    from chemo_agent_product.agents import AgentService

    return await AgentService(w).read(p, context_id, run_id)


@router.get("/contexts/{context_id}/agent-runs")
async def list_agents(
    context_id: UUID,
    p: PrincipalDep,
    w: WorkflowDep,
    kind: Annotated[str, Query(pattern="^(RECOMMENDATION|REVIEWER)$")],
    revision_id: UUID | None = None,
):
    from chemo_agent_product.agents import AgentService

    return await AgentService(w).list_runs(p, context_id, kind, revision_id)


@router.post("/contexts/{context_id}/actions")
async def action(
    context_id: UUID,
    request: ActionInput,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    # Viewing and comparison only log activity; neither adopts a plan.
    async with w.pool.acquire() as c, c.transaction():
        context = await w.scoped_context(c, context_id, p)
        cmd, cached = await w.command(
            c,
            p,
            request.kind,
            idempotency_key,
            {"context_id": str(context_id), **request.model_dump(mode="json")},
        )
        if cached:
            return cached
        for candidate_id in request.candidate_ids:
            await w.candidate(c, context_id, p, candidate_id)
        result = {"action": request.kind, "candidate_ids": [str(i) for i in request.candidate_ids]}
        await w.audit(c, p, context, f"CANDIDATE_{request.kind}", context_id, result)
        await w.complete(c, cmd, result, "launch_context", context_id)
        return result
