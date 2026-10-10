from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from chemo_agent_product.core.schemas import CapabilityStatus
from chemo_agent_product.persistence.health import database_available

router = APIRouter()


@router.get("/health/live")
async def health() -> dict[str, str]:
    return {"status": "ok", "product": "化疗智能体"}


@router.get("/health/ready")
async def ready(request: Request):
    state = request.app.state
    supervisor = state.worker_supervisor
    required = bool(
        state.settings.worker_enabled and (state.workflow or state.settings.launch_signing_key)
    )
    worker = (
        (supervisor.status if supervisor.status != "RUNNING" or supervisor.healthy else "STOPPED")
        if supervisor
        else ("NOT_CONFIGURED" if required else "DISABLED")
    )
    database_ready = state.database_status == "TEST_DOUBLE" or (
        state.database_status == "CONNECTED"
        and state.database_pool is not None
        and await database_available(state.database_pool)
    )
    healthy = database_ready and (not required or bool(supervisor and supervisor.healthy))
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ready" if healthy else "not_ready",
            "database": state.database_status if database_ready else "UNAVAILABLE",
            "worker": worker,
        },
    )


@router.get("/api/v1/host-contract")
async def host_contract(request: Request):
    return {
        "version": "1",
        "trusted_origins": request.app.state.settings.trusted_host_origins,
        "token_transport": "POST_MESSAGE_MEMORY_ONLY",
        "hospital_sso": "NOT_CONFIGURED",
    }


@router.get("/api/v1/status", response_model=CapabilityStatus)
async def status(request: Request) -> CapabilityStatus:
    return CapabilityStatus(
        mode="WORKFLOW_TEST"
        if request.app.state.workflow
        else "READ_ONLY_TEST"
        if request.app.state.settings.read_enabled
        else "NOT_APPROVED",
        database=request.app.state.database_status,
        patient_context=request.app.state.context_status,
        model="CONFIGURED_NOT_VERIFIED"
        if request.app.state.settings.model_configured
        else "NOT_CONNECTED",
        hospital="CONFIGURED_NOT_VERIFIED"
        if request.app.state.settings.hospital_adapter_config
        else "NOT_CONNECTED",
    )
