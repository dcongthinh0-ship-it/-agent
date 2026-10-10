"""Database-backed worker with leases, recovery and generation fencing."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from uuid import uuid4

import asyncpg
import httpx

from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.observability import emit, log_context
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.integrations.hospital.reader import (
    ConfiguredHospitalReader,
    HospitalReader,
)
from chemo_agent_product.modules.knowledge.service import load_inputs
from chemo_agent_product.modules.recommendation.preparation import PreparationService

from . import job_repository as repository

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        pool: asyncpg.Pool,
        settings: Settings,
        reader: HospitalReader | None = None,
        *,
        knowledge_loader=load_inputs,
    ):
        self.pool, self.settings = pool, settings
        self.reader = reader or ConfiguredHospitalReader(settings.hospital_adapter_config)
        self.knowledge_loader = knowledge_loader
        self.owner = f"patient-worker:{uuid4()}"
        self.agent_handler = None
        self.preparation = PreparationService(self)

    async def claim(self):
        async with self.pool.acquire() as c, c.transaction():
            dead = await repository.expire_exhausted_jobs(c)
            for row in dead:
                if row["prepare_run_id"]:
                    await repository.fail_exhausted_preparation(c, row["prepare_run_id"])
                if row["agent_run_id"]:
                    await repository.fail_exhausted_agent(c, row["agent_run_id"])
                    await repository.fail_exhausted_tool_calls(c, row["agent_run_id"])
            job = await repository.find_available_job(c)
            claimed = (
                await repository.claim_job(
                    c, job["id"], self.owner, uuid4(), self.settings.worker_lease_seconds
                )
                if job
                else None
            )
        for row in dead:
            emit(
                logger,
                "job_dead_letter",
                level=logging.ERROR,
                job_id=row["id"],
                prepare_run_id=row["prepare_run_id"],
                agent_run_id=row["agent_run_id"],
                error_code="LEASE_RECOVERY_EXHAUSTED",
            )
        return claimed

    async def fenced(self, c, job):
        current = await repository.get_locked_lease(c, job["id"])
        if (
            not current
            or current["status"] != "RUNNING"
            or current["lease_token"] != job["lease_token"]
            or current["lease_epoch"] != job["lease_epoch"]
            or not current["lease_is_live"]
        ):
            raise BusinessError("JOB_LEASE_LOST", "任务租约已交给其他执行者", 409)

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(max(5, self.settings.worker_lease_seconds / 3))
            await self.renew(job)

    async def renew(self, job):
        async with self.pool.acquire() as c:
            result = await repository.renew_lease(
                c,
                job["id"],
                job["lease_token"],
                job["lease_epoch"],
                self.settings.worker_lease_seconds,
            )
        if result != "UPDATE 1":
            raise BusinessError("JOB_LEASE_LOST", "任务租约已经失效", 409)
        emit(logger, "lease_renewed", level=logging.DEBUG)

    async def execute(self, job):
        if job["job_kind"] == "PREPARE":
            await self.prepare(job)
        elif self.agent_handler:
            await self.agent_handler(job, self)
        else:
            raise BusinessError("AGENT_NOT_CONFIGURED", "智能体执行器尚未配置", 503)

    async def prepare(self, job):
        return await self.preparation.prepare(job)

    async def once(self):
        job = await self.claim()
        if not job:
            return False
        async with self.pool.acquire() as c:
            diagnostic = await repository.get_job_diagnostics(c, job["id"])
        with log_context(
            request_id=uuid4(),
            job_id=job["id"],
            context_id=diagnostic["context_id"] if diagnostic else None,
            agent_run_id=job["agent_run_id"],
        ):
            return await self.run_claimed(job)

    async def run_claimed(self, job):
        started = perf_counter()
        emit(
            logger,
            "job_claimed",
            job_kind=job["job_kind"],
            attempt=job["attempt_count"],
            prepare_run_id=job["prepare_run_id"],
        )
        heartbeat = asyncio.create_task(self.heartbeat(job))
        execution = asyncio.create_task(self.execute(job))
        try:
            done, _ = await asyncio.wait(
                {heartbeat, execution}, return_when=asyncio.FIRST_COMPLETED
            )
            if execution in done:
                await execution
            else:
                # A failed/expired renewal cancels in-flight work. Leave the old
                # lease for database-clock recovery; never update another owner.
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                try:
                    await heartbeat
                    raise RuntimeError("Heartbeat returned unexpectedly")
                except Exception as exc:
                    emit(
                        logger,
                        "lease_lost"
                        if isinstance(exc, BusinessError) and exc.code == "JOB_LEASE_LOST"
                        else "heartbeat_failed",
                        level=logging.ERROR,
                        exc=exc,
                        error_code="JOB_LEASE_LOST"
                        if isinstance(exc, BusinessError)
                        else "HEARTBEAT_FAILED",
                    )
                emit(
                    logger,
                    "job_abandoned",
                    level=logging.WARNING,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
                return True
        except asyncio.CancelledError:
            emit(logger, "job_cancelled", duration_ms=round((perf_counter() - started) * 1000, 2))
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "HOSPITAL_TIMEOUT"
                if isinstance(exc, httpx.TimeoutException)
                else "TASK_FAILED"
            )
            retry = (
                isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
                and job["attempt_count"] < job["max_attempts"]
            )
            emit(
                logger,
                "job_failed",
                level=logging.WARNING if retry else logging.ERROR,
                exc=exc,
                error_code=code,
                attempt=job["attempt_count"],
            )
            async with self.pool.acquire() as c, c.transaction():
                try:
                    await self.fenced(c, job)
                except BusinessError as lease_error:
                    emit(
                        logger,
                        "job_abandoned",
                        level=logging.WARNING,
                        exc=lease_error,
                        error_code=lease_error.code,
                    )
                    return True
                await repository.schedule_job_outcome(
                    c,
                    job["id"],
                    "RETRY_WAIT" if retry else "FAILED",
                    code,
                    2 ** job["attempt_count"],
                )
                if job["prepare_run_id"]:
                    await repository.fail_preparation(
                        c,
                        job["prepare_run_id"],
                        "QUEUED" if retry else "FAILED",
                        code,
                        f"job:{job['id']}",
                    )
        finally:
            heartbeat.cancel()
            execution.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)
        async with self.pool.acquire() as c:
            outcome = await repository.get_job_diagnostics(c, job["id"])
        emit(
            logger,
            "job_finished",
            status=outcome["status"] if outcome else None,
            error_code=outcome["last_error_code"] if outcome else None,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return True

    async def loop(self):
        while True:
            worked = await self.once()
            if not worked:
                await asyncio.sleep(self.settings.worker_poll_seconds)
