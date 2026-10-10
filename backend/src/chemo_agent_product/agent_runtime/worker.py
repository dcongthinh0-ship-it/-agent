"""Database-backed worker with leases, recovery and generation fencing."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import httpx

from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.integrations.hospital.reader import (
    ConfiguredHospitalReader,
    HospitalReader,
)
from chemo_agent_product.modules.knowledge.service import load_inputs
from chemo_agent_product.modules.recommendation.preparation import PreparationService

from . import job_repository as repository


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
            if not job:
                return None
            return await repository.claim_job(
                c, job["id"], self.owner, uuid4(), self.settings.worker_lease_seconds
            )

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
            async with self.pool.acquire() as c:
                await repository.renew_lease(
                    c,
                    job["id"],
                    job["lease_token"],
                    job["lease_epoch"],
                    self.settings.worker_lease_seconds,
                )

    async def prepare(self, job):
        return await self.preparation.prepare(job)

    async def once(self):
        job = await self.claim()
        if not job:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(job))
        try:
            if job["job_kind"] == "PREPARE":
                await self.prepare(job)
            elif self.agent_handler:
                await self.agent_handler(job, self)
            else:
                raise BusinessError("AGENT_NOT_CONFIGURED", "智能体执行器尚未配置", 503)
        except asyncio.CancelledError:
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
            async with self.pool.acquire() as c, c.transaction():
                try:
                    await self.fenced(c, job)
                except BusinessError:
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
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def loop(self):
        while True:
            worked = await self.once()
            if not worked:
                await asyncio.sleep(self.settings.worker_poll_seconds)
