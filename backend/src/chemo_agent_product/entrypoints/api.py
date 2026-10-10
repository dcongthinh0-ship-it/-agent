from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import asyncpg
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from chemo_agent_product.agent_runtime.api import router as agent_router
from chemo_agent_product.agent_runtime.service import AgentService
from chemo_agent_product.agent_runtime.supervision import WorkerSupervisor
from chemo_agent_product.agent_runtime.worker import Worker
from chemo_agent_product.core.api import router as system_router
from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.http import error
from chemo_agent_product.core.observability import configure_logging, emit, register_routes
from chemo_agent_product.core.request_logging import RequestLoggingMiddleware
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

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    reader: CatalogReader | None = None,
    context_reader: ContextReader | None = None,
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        register_routes(app)
        configure_logging(config)
        app.state.reader = reader
        app.state.database_status = "TEST_DOUBLE" if reader else "UNCONFIGURED"
        app.state.context_reader = context_reader
        app.state.workflow = None
        app.state.agents = None
        app.state.worker_supervisor = None
        app.state.database_pool = None
        app.state.context_status = (
            "TEST_ONLY" if context_reader and config.read_enabled else "NOT_CONNECTED"
        )
        pool: asyncpg.Pool | None = None
        runtime_pool: asyncpg.Pool | None = None
        if reader is None and config.database_url and config.read_enabled:
            try:
                pool = await open_pool(config.database_url.get_secret_value())
                app.state.database_pool = pool
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
                        app.state.worker_supervisor = WorkerSupervisor(worker)
                        await app.state.worker_supervisor.start()
            except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                app.state.database_status = "UNAVAILABLE"
                emit(
                    logger,
                    "database_connection_failed",
                    level=logging.ERROR,
                    exc=exc,
                    component="catalog",
                )
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
                except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                    app.state.context_status = "NOT_CONNECTED"
                    emit(
                        logger,
                        "database_connection_failed",
                        level=logging.ERROR,
                        exc=exc,
                        component="context",
                    )
        try:
            emit(logger, "application_started")
            yield
        finally:
            if app.state.worker_supervisor:
                await app.state.worker_supervisor.stop()
            if pool is not None:
                await pool.close()
            if runtime_pool is not None:
                await runtime_pool.close()
            emit(logger, "application_stopped")

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
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(asyncpg.PostgresError)
    async def database_error(request: Request, exc: asyncpg.PostgresError) -> JSONResponse:
        emit(
            logger,
            "database_error",
            level=logging.ERROR,
            exc=exc,
            request_id=getattr(request.state, "request_id", None),
            context_id=request.path_params.get("context_id"),
            error_code="CATALOG_UNAVAILABLE",
        )
        return error(503, "CATALOG_UNAVAILABLE", "方案目录查询失败，请稍后重试")

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError):
        emit(
            logger,
            "business_error",
            level=logging.ERROR
            if exc.status >= 500
            else logging.WARNING
            if exc.status in {401, 403, 409}
            else logging.INFO,
            exc=exc,
            request_id=getattr(request.state, "request_id", None),
            context_id=request.path_params.get("context_id"),
            error_code=exc.code,
            http_status=exc.status,
        )
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception):
        # The request middleware records only safe diagnostics. Never send the
        # exception message or stack to the browser; preserve the correlation ID.
        response = error(500, "INTERNAL_ERROR", "系统处理失败，请稍后重试")
        if identifier := getattr(request.state, "request_id", None):
            response.headers["X-Request-ID"] = identifier
        return response

    app.include_router(system_router)
    app.include_router(catalog_router)
    app.include_router(context_router)
    app.include_router(recommendation_router)
    app.include_router(patient_router)
    app.include_router(agent_router)
    app.include_router(management_router)
    register_routes(app)
    return app


app = create_app()


def run() -> None:
    config = Settings()
    uvicorn.run(
        "chemo_agent_product.entrypoints.api:app",
        host=config.host,
        port=config.port,
        reload=config.environment == "local",
        access_log=False,
    )
