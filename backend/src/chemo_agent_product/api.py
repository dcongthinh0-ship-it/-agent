from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
import uvicorn
from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from chemo_agent_product.catalog import CatalogReader, PostgresCatalogReader
from chemo_agent_product.config import Settings
from chemo_agent_product.contracts import (
    CapabilityStatus,
    ContextReadout,
    EvidenceDetail,
    EvidenceList,
    RegimenDetail,
    RegimenPage,
)
from chemo_agent_product.database import open_pool
from chemo_agent_product.patient_api import principal as authenticated_principal
from chemo_agent_product.patient_api import router as patient_router
from chemo_agent_product.runtime import ContextReader, PostgresTestContextReader
from chemo_agent_product.security import BusinessError
from chemo_agent_product.worker import Worker
from chemo_agent_product.workflow import Workflow


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def create_app(
    settings: Settings | None = None,
    reader: CatalogReader | None = None,
    context_reader: ContextReader | None = None,
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.reader = reader
        app.state.database_status = "TEST_DOUBLE" if reader else "UNCONFIGURED"
        app.state.context_reader = context_reader
        app.state.workflow = None
        worker_task = None
        app.state.context_status = (
            "TEST_ONLY" if context_reader and config.read_enabled else "NOT_CONNECTED"
        )
        pool: asyncpg.Pool | None = None
        runtime_pool: asyncpg.Pool | None = None
        if reader is None and config.database_url and config.read_enabled:
            try:
                pool = await open_pool(config.database_url.get_secret_value())
                app.state.reader = PostgresCatalogReader(pool)
                app.state.database_status = "CONNECTED"
                migrated = await pool.fetchval(
                    "SELECT to_regclass('ops.product_migration') IS NOT NULL"
                )
                if migrated and config.launch_signing_key:
                    app.state.workflow = Workflow(pool, config)
                    app.state.context_status = "TEST_ONLY"
                    if config.worker_enabled:
                        worker = Worker(pool, config)
                        worker_task = asyncio.create_task(worker.loop())
            except (OSError, asyncpg.PostgresError, TimeoutError):
                # 健康状态保持可读，前端可以明确呈现数据库不可用。
                app.state.database_status = "UNAVAILABLE"
        if context_reader is None and config.runtime_test_database_url and config.read_enabled:
            runtime_dsn = config.runtime_test_database_url.get_secret_value()
            runtime_name = urlsplit(runtime_dsn).path.rsplit("/", 1)[-1].lower()
            catalog_dsn = config.database_url.get_secret_value() if config.database_url else None
            if "test" in runtime_name and runtime_dsn != catalog_dsn:
                try:
                    runtime_pool = await asyncpg.create_pool(
                        dsn=runtime_dsn, min_size=1, max_size=3, command_timeout=6
                    )
                    app.state.context_reader = PostgresTestContextReader(runtime_pool)
                    app.state.context_status = "TEST_ONLY"
                except (OSError, asyncpg.PostgresError, TimeoutError):
                    app.state.context_status = "NOT_CONNECTED"
        try:
            yield
        finally:
            if worker_task:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            if pool is not None:
                await pool.close()
            if runtime_pool is not None:
                await runtime_pool.close()

    app = FastAPI(title="化疗智能体 API", version="0.2.0", lifespan=lifespan)
    app.state.settings = config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            *config.trusted_host_origins,
        ]
        if config.read_enabled
        else [],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type", "Authorization", "Idempotency-Key"],
    )

    async def get_reader(request: Request) -> CatalogReader | JSONResponse:
        if not config.read_enabled:
            return error(503, "CAPABILITY_NOT_APPROVED", "只读目录尚未配置可信认证入口")
        current: CatalogReader | None = request.app.state.reader
        if current is None:
            return error(503, "CATALOG_UNAVAILABLE", "方案目录暂不可读取")
        return current

    async def get_context_reader(request: Request) -> ContextReader | JSONResponse:
        if not config.read_enabled:
            return error(503, "CAPABILITY_NOT_APPROVED", "尚未配置可信院方身份与上下文授权")
        current: ContextReader | None = request.app.state.context_reader
        if current is None:
            return error(503, "PATIENT_CONTEXT_NOT_CONNECTED", "患者上下文读取尚未接入")
        return current

    @app.exception_handler(asyncpg.PostgresError)
    async def database_error(_request: Request, _exc: asyncpg.PostgresError) -> JSONResponse:
        return error(503, "CATALOG_UNAVAILABLE", "方案目录查询失败，请稍后重试")

    @app.exception_handler(BusinessError)
    async def business_error(_request: Request, exc: BusinessError):
        return error(exc.status, exc.code, exc.message)

    @app.get("/health/live")
    async def health() -> dict[str, str]:
        return {"status": "ok", "product": "化疗智能体"}

    @app.get("/api/v1/status", response_model=CapabilityStatus)
    async def status(request: Request) -> CapabilityStatus:
        return CapabilityStatus(
            mode="WORKFLOW_TEST"
            if request.app.state.workflow
            else "READ_ONLY_TEST"
            if config.read_enabled
            else "NOT_APPROVED",
            database=request.app.state.database_status,
            patient_context=request.app.state.context_status,
            model="CONFIGURED_NOT_VERIFIED" if config.model_configured else "NOT_CONNECTED",
            hospital="CONFIGURED_NOT_VERIFIED"
            if config.hospital_adapter_config
            else "NOT_CONNECTED",
        )

    @app.get("/api/v1/contexts/{context_id}", response_model=None)
    async def context_readout(
        context_id: UUID,
        request: Request,
        context: ContextReader | JSONResponse = Depends(get_context_reader),  # noqa: B008
    ) -> ContextReadout | JSONResponse:
        if request.app.state.workflow:
            who = await authenticated_principal(request, request.headers.get("Authorization"))
            return await request.app.state.workflow.read_context(who, context_id)
        if isinstance(context, JSONResponse):
            return context
        result = await context.get_context(context_id)
        return result or error(404, "CONTEXT_NOT_FOUND", "该测试上下文不存在或已不可读取")

    @app.get("/api/v1/regimens", response_model=RegimenPage)
    async def list_regimens(
        reader: CatalogReader | JSONResponse = Depends(get_reader),  # noqa: B008
        search: Annotated[str, Query(max_length=100)] = "",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> RegimenPage | JSONResponse:
        if isinstance(reader, JSONResponse):
            return reader
        return await reader.list_regimens(search.strip(), page, page_size)

    @app.get("/api/v1/regimens/{regimen_id}/versions/{version_id}", response_model=RegimenDetail)
    async def regimen_detail(
        regimen_id: UUID,
        version_id: UUID,
        reader: CatalogReader | JSONResponse = Depends(get_reader),  # noqa: B008
    ) -> RegimenDetail | JSONResponse:
        if isinstance(reader, JSONResponse):
            return reader
        detail = await reader.get_regimen(regimen_id, version_id)
        return detail or error(404, "VERSION_NOT_FOUND", "未找到该方案的指定版本")

    @app.get(
        "/api/v1/regimens/{regimen_id}/versions/{version_id}/evidence",
        response_model=EvidenceList,
    )
    async def regimen_evidence(
        regimen_id: UUID,
        version_id: UUID,
        reader: CatalogReader | JSONResponse = Depends(get_reader),  # noqa: B008
    ) -> EvidenceList | JSONResponse:
        if isinstance(reader, JSONResponse):
            return reader
        evidence = await reader.list_evidence(regimen_id, version_id)
        return evidence or error(404, "VERSION_NOT_FOUND", "未找到该方案的指定版本")

    @app.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceDetail)
    async def evidence_detail(
        evidence_id: UUID,
        reader: CatalogReader | JSONResponse = Depends(get_reader),  # noqa: B008
    ) -> EvidenceDetail | JSONResponse:
        if isinstance(reader, JSONResponse):
            return reader
        evidence = await reader.get_evidence(evidence_id)
        return evidence or error(404, "EVIDENCE_NOT_FOUND", "未找到该证据版本")

    app.include_router(patient_router)
    return app


app = create_app()


def run() -> None:
    config = Settings()
    uvicorn.run(
        "chemo_agent_product.api:app",
        host=config.host,
        port=config.port,
        reload=config.environment == "local",
    )
