from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from chemo_agent_product.core.dependencies import CommandKey, PrincipalDep, WorkflowDep
from chemo_agent_product.core.dependencies import principal as authenticated_principal
from chemo_agent_product.core.http import error
from chemo_agent_product.core.read_dependencies import ContextReaderDep
from chemo_agent_product.modules.clinical_context.schemas import (
    ContextReadout,
    LaunchInput,
    RefreshInput,
)

router = APIRouter(prefix="/api/v1")


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


@router.get("/contexts/{context_id}/preparation-status")
async def preparation_status(context_id: UUID, p: PrincipalDep, w: WorkflowDep):
    return await w.preparation_status(p, context_id)


@router.get("/contexts/{context_id}", response_model=None)
async def context_readout(
    context_id: UUID, request: Request, context: ContextReaderDep
) -> ContextReadout | JSONResponse:
    if request.app.state.workflow:
        who = await authenticated_principal(request, request.headers.get("Authorization"))
        return await request.app.state.workflow.read_context(who, context_id)
    if isinstance(context, JSONResponse):
        return context
    result = await context.get_context(context_id)
    return result or error(404, "CONTEXT_NOT_FOUND", "该测试上下文不存在或已不可读取")
