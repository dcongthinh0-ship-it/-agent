from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from chemo_agent_product.core.http import error
from chemo_agent_product.core.read_dependencies import (
    CatalogReaderDep,
)
from chemo_agent_product.modules.knowledge.schemas import EvidenceDetail, EvidenceList
from chemo_agent_product.modules.regimen.schemas import RegimenDetail, RegimenPage

router = APIRouter()


@router.get("/api/v1/regimens", response_model=RegimenPage)
async def list_regimens(
    reader: CatalogReaderDep,
    search: Annotated[str, Query(max_length=100)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RegimenPage | JSONResponse:
    if isinstance(reader, JSONResponse):
        return reader
    return await reader.list_regimens(search.strip(), page, page_size)


@router.get("/api/v1/regimens/{regimen_id}/versions/{version_id}", response_model=RegimenDetail)
async def regimen_detail(
    regimen_id: UUID, version_id: UUID, reader: CatalogReaderDep
) -> RegimenDetail | JSONResponse:
    if isinstance(reader, JSONResponse):
        return reader
    detail = await reader.get_regimen(regimen_id, version_id)
    return detail or error(404, "VERSION_NOT_FOUND", "未找到该方案的指定版本")


@router.get(
    "/api/v1/regimens/{regimen_id}/versions/{version_id}/evidence", response_model=EvidenceList
)
async def regimen_evidence(
    regimen_id: UUID, version_id: UUID, reader: CatalogReaderDep
) -> EvidenceList | JSONResponse:
    if isinstance(reader, JSONResponse):
        return reader
    evidence = await reader.list_evidence(regimen_id, version_id)
    return evidence or error(404, "VERSION_NOT_FOUND", "未找到该方案的指定版本")


@router.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceDetail)
async def evidence_detail(
    evidence_id: UUID, reader: CatalogReaderDep
) -> EvidenceDetail | JSONResponse:
    if isinstance(reader, JSONResponse):
        return reader
    evidence = await reader.get_evidence(evidence_id)
    return evidence or error(404, "EVIDENCE_NOT_FOUND", "未找到该证据版本")
