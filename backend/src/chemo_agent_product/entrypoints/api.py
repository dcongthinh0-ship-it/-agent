from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import asyncpg
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from chemo_agent_product.agent_runtime.api import router as agent_router
from chemo_agent_product.agent_runtime.service import AgentService
from chemo_agent_product.agent_runtime.worker import Worker
from chemo_agent_product.core.api import router as system_router
from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.http import error
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.modules.clinical_context.api import router as context_router
from chemo_agent_product.modules.clinical_context.legacy_reader import (
    ContextReader,
    PostgresTestContextReader,
)
from chemo_agent_product.modules.clinical_context.workflow import Workflow
from chemo_agent_product.modules.management.api import router as management_router
from chemo_agent_product.modules.patient_regimen.api import router as patient_router
from chemo_agent_product.modules.recommendation.api import router as recommendation_router
from chemo_agent_product.modules.regimen.api import router as catalog_router
from chemo_agent_product.modules.regimen.reader import CatalogReader, PostgresCatalogReader
from chemo_agent_product.persistence.database import open_pool
from chemo_agent_product.persistence.health import workflow_schema_ready


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
        app.state.agents = None
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
                migrated = await workflow_schema_ready(pool)
                if migrated and config.launch_signing_key:
                    app.state.workflow = Workflow(pool, config)
                    app.state.agents = AgentService(app.state.workflow)
                    app.state.context_status = "TEST_ONLY"
                    if config.worker_enabled:
                        worker = Worker(pool, config)
                        worker.agent_handler = app.state.agents.run_job
                        worker_task = asyncio.create_task(worker.loop())
            except (OSError, asyncpg.PostgresError, TimeoutError):
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

    @app.exception_handler(asyncpg.PostgresError)
    async def database_error(_request: Request, _exc: asyncpg.PostgresError) -> JSONResponse:
        return error(503, "CATALOG_UNAVAILABLE", "方案目录查询失败，请稍后重试")

    @app.exception_handler(BusinessError)
    async def business_error(_request: Request, exc: BusinessError):
        return error(exc.status, exc.code, exc.message)

    app.include_router(system_router)
    app.include_router(catalog_router)
    app.include_router(context_router)
    app.include_router(recommendation_router)
    app.include_router(patient_router)
    app.include_router(agent_router)
    app.include_router(management_router)
    return app


app = create_app()


def run() -> None:
    config = Settings()
    uvicorn.run(
        "chemo_agent_product.entrypoints.api:app",
        host=config.host,
        port=config.port,
        reload=config.environment == "local",
    )
