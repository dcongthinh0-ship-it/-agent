from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from chemo_agent_product.core.dependencies import CommandKey, PrincipalDep, WorkflowDep
from chemo_agent_product.modules.recommendation.schemas import ActionInput

router = APIRouter(prefix="/api/v1")


@router.get("/contexts/{context_id}/candidates/{candidate_id}")
async def detail(context_id: UUID, candidate_id: UUID, p: PrincipalDep, w: WorkflowDep):
    return await w.detail(p, context_id, candidate_id)


@router.get("/contexts/{context_id}/candidates/{candidate_id}/evidence")
async def evidence(context_id: UUID, candidate_id: UUID, p: PrincipalDep, w: WorkflowDep):
    return await w.candidate_evidence(p, context_id, candidate_id)


@router.post("/contexts/{context_id}/actions")
async def action(
    context_id: UUID,
    request: ActionInput,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    return await w.action(p, context_id, request, idempotency_key)
