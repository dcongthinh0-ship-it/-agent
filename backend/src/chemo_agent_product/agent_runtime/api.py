from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from chemo_agent_product.agent_runtime.contracts import AgentRequest
from chemo_agent_product.core.dependencies import CommandKey, PrincipalDep, WorkflowDep

router = APIRouter(prefix="/api/v1")


@router.post("/contexts/{context_id}/agent-runs")
async def start_agent(
    context_id: UUID,
    request: AgentRequest,
    p: PrincipalDep,
    w: WorkflowDep,
    idempotency_key: CommandKey,
):
    from chemo_agent_product.agent_runtime.service import AgentService

    return await AgentService(w).enqueue(p, context_id, request, idempotency_key)


@router.get("/contexts/{context_id}/agent-runs/{run_id}")
async def read_agent(context_id: UUID, run_id: UUID, p: PrincipalDep, w: WorkflowDep):
    from chemo_agent_product.agent_runtime.service import AgentService

    return await AgentService(w).read(p, context_id, run_id)


@router.get("/contexts/{context_id}/agent-runs")
async def list_agents(
    context_id: UUID,
    p: PrincipalDep,
    w: WorkflowDep,
    kind: Annotated[str, Query(pattern="^(RECOMMENDATION|REVIEWER)$")],
    revision_id: UUID | None = None,
):
    from chemo_agent_product.agent_runtime.service import AgentService

    return await AgentService(w).list_runs(p, context_id, kind, revision_id)
