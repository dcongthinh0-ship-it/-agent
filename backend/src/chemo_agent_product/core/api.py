from __future__ import annotations

from fastapi import APIRouter, Request

from chemo_agent_product.core.schemas import CapabilityStatus

router = APIRouter()


@router.get("/health/live")
async def health() -> dict[str, str]:
    return {"status": "ok", "product": "化疗智能体"}


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
