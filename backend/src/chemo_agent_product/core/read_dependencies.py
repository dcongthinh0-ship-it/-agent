from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from chemo_agent_product.core.http import error
from chemo_agent_product.modules.clinical_context.legacy_reader import ContextReader
from chemo_agent_product.modules.regimen.reader import CatalogReader


async def get_reader(request: Request) -> CatalogReader | JSONResponse:
    if not request.app.state.settings.read_enabled:
        return error(503, "CAPABILITY_NOT_APPROVED", "只读目录尚未配置可信认证入口")
    current: CatalogReader | None = request.app.state.reader
    if current is None:
        return error(503, "CATALOG_UNAVAILABLE", "方案目录暂不可读取")
    return current


async def get_context_reader(request: Request) -> ContextReader | JSONResponse:
    if not request.app.state.settings.read_enabled:
        return error(503, "CAPABILITY_NOT_APPROVED", "尚未配置可信院方身份与上下文授权")
    current: ContextReader | None = request.app.state.context_reader
    if current is None:
        return error(503, "PATIENT_CONTEXT_NOT_CONNECTED", "患者上下文读取尚未接入")
    return current


CatalogReaderDep = Annotated[CatalogReader | JSONResponse, Depends(get_reader)]
ContextReaderDep = Annotated[ContextReader | JSONResponse, Depends(get_context_reader)]
