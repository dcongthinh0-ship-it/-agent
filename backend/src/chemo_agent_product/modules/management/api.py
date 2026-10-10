"""HTTP endpoints delegate to authorized overview use cases."""

from fastapi import APIRouter

from chemo_agent_product.core.dependencies import PrincipalDep, WorkflowDep

from . import service

router = APIRouter(prefix="/api/v1")


@router.get("/knowledge/overview")
async def knowledge_overview(p: PrincipalDep, w: WorkflowDep):
    return await service.knowledge_overview(p, w)


@router.get("/operations/overview")
async def operations_overview(p: PrincipalDep, w: WorkflowDep):
    return await service.operations_overview(p, w)
