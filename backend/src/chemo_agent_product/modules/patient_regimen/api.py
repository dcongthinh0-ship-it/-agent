from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from chemo_agent_product.core.dependencies import CommandKey, PrincipalDep, WorkflowDep
from chemo_agent_product.modules.patient_regimen.schemas import ConfirmInput, SaveInput

router = APIRouter(prefix="/api/v1")


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


@router.get("/contexts/{context_id}/instances/{instance_id}/hospital-readiness")
async def hospital_readiness(context_id: UUID, instance_id: UUID, p: PrincipalDep, w: WorkflowDep):
    from chemo_agent_product.modules.patient_regimen.readiness import readiness

    return await readiness(w, p, context_id, instance_id)


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
    return await w.confirm_with_review(p, context_id, instance_id, request, idempotency_key)
