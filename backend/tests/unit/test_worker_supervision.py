"""Failure injection for task ownership, renewal, recovery and shutdown."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import httpx
import pytest

from chemo_agent_product.agent_runtime import job_repository as repository
from chemo_agent_product.agent_runtime.supervision import WorkerSupervisor
from chemo_agent_product.agent_runtime.worker import Worker
from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.entrypoints.api import create_app


class Pool:
    async def fetchval(self, _query):
        return 1

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self


@pytest.fixture
def worker(monkeypatch):
    worker = Worker(Pool(), Settings(_env_file=None, environment="test", worker_enabled=False))
    job = {
        "id": uuid4(),
        "agent_run_id": None,
        "prepare_run_id": uuid4(),
        "job_kind": "PREPARE",
        "attempt_count": 1,
        "max_attempts": 3,
        "lease_token": uuid4(),
        "lease_epoch": 1,
    }
    worker.claim = AsyncMock(return_value=job)
    worker.fenced = AsyncMock()
    monkeypatch.setattr(
        repository,
        "get_job_diagnostics",
        AsyncMock(
            return_value={
                "context_id": uuid4(),
                "status": "SUCCEEDED",
                "last_error_code": None,
            }
        ),
    )
    monkeypatch.setattr(repository, "schedule_job_outcome", AsyncMock())
    monkeypatch.setattr(repository, "fail_preparation", AsyncMock())
    return worker, job


@pytest.mark.asyncio
async def test_zero_row_renewal_detects_lost_lease(worker, monkeypatch):
    worker, job = worker
    renew = AsyncMock(return_value="UPDATE 0")
    monkeypatch.setattr(repository, "renew_lease", renew)
    with pytest.raises(BusinessError, match="JOB_LEASE_LOST"):
        await worker.renew(job)
    assert renew.await_args.args[2:] == (job["lease_token"], job["lease_epoch"], 180)


@pytest.mark.asyncio
async def test_successful_renewal_keeps_lease_token_and_epoch(worker, monkeypatch):
    worker, job = worker
    monkeypatch.setattr(repository, "renew_lease", AsyncMock(return_value="UPDATE 1"))
    await worker.renew(job)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [False, True])
async def test_failed_heartbeat_cancels_work_without_updating_lease_owner(worker, lost):
    worker, _job = worker
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work(_job):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def heartbeat(_job):
        await started.wait()
        if lost:
            raise BusinessError("JOB_LEASE_LOST", "租约失效", 409)
        raise asyncpg.ConnectionDoesNotExistError("CONTRACT_ONLY_PRIVATE_SENTINEL")

    worker.prepare, worker.heartbeat = work, heartbeat
    assert await asyncio.wait_for(worker.once(), timeout=1)
    assert cancelled.is_set()
    repository.schedule_job_outcome.assert_not_awaited()
    repository.fail_preparation.assert_not_awaited()


@pytest.mark.asyncio
async def test_finished_work_wins_heartbeat_zero_row_race(worker):
    worker, _job = worker
    completed = asyncio.Event()

    async def work(_job):
        completed.set()

    async def heartbeat(_job):
        await completed.wait()
        raise BusinessError("JOB_LEASE_LOST", "完成后续租未更新", 409)

    worker.prepare, worker.heartbeat = work, heartbeat
    assert await worker.once()
    repository.schedule_job_outcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_network_failure_preserves_existing_retry_contract(worker):
    worker, job = worker
    worker.prepare = AsyncMock(side_effect=httpx.ReadTimeout("CONTRACT_ONLY_PRIVATE_SENTINEL"))
    assert await worker.once()
    args = repository.schedule_job_outcome.await_args.args
    assert args[1:] == (job["id"], "RETRY_WAIT", "HOSPITAL_TIMEOUT", 2)
    assert repository.fail_preparation.await_args.args[2:4] == ("QUEUED", "HOSPITAL_TIMEOUT")


@pytest.mark.asyncio
async def test_shutdown_cancels_both_inflight_children_without_marking_failure(worker):
    worker, _job = worker
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work(_job):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker.prepare = work
    task = asyncio.create_task(worker.once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    repository.schedule_job_outcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_failure_is_retried_and_supervisor_remains_alive(worker):
    worker, _job = worker
    recovered = asyncio.Event()
    claims = 0

    async def claim():
        nonlocal claims
        claims += 1
        if claims == 1:
            raise asyncpg.ConnectionDoesNotExistError("CONTRACT_ONLY_PRIVATE_SENTINEL")
        recovered.set()
        await asyncio.Event().wait()

    worker.claim = claim
    supervisor = WorkerSupervisor(worker)
    await supervisor.start()
    assert supervisor.status == "RETRYING" and not supervisor.healthy
    await asyncio.wait_for(recovered.wait(), timeout=2)
    assert claims == 2 and supervisor.restart_count == 1 and supervisor.healthy
    await supervisor.stop()
    assert supervisor.status == "STOPPED" and supervisor.task.done()


@pytest.mark.asyncio
async def test_unexpected_worker_return_is_restarted(worker):
    worker, _job = worker
    recovered = asyncio.Event()
    calls = 0

    async def loop():
        nonlocal calls
        calls += 1
        if calls == 1:
            return
        recovered.set()
        await asyncio.Event().wait()

    worker.loop = loop
    supervisor = WorkerSupervisor(worker)
    await supervisor.start()
    await asyncio.wait_for(recovered.wait(), timeout=2)
    assert calls == 2 and supervisor.healthy
    await supervisor.stop()


@pytest.mark.asyncio
async def test_child_cancellation_is_restarted_but_shutdown_is_not(worker):
    worker, _job = worker
    recovered = asyncio.Event()
    calls = 0

    async def loop():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError()
        recovered.set()
        await asyncio.Event().wait()

    worker.loop = loop
    supervisor = WorkerSupervisor(worker)
    await supervisor.start()
    await asyncio.wait_for(recovered.wait(), timeout=2)
    assert supervisor.restart_count == 1 and supervisor.healthy
    await supervisor.stop()
    assert supervisor.status == "STOPPED" and calls == 2


@pytest.mark.asyncio
async def test_readiness_is_unavailable_during_worker_recovery_and_liveness_stays_ok(worker):
    from httpx import ASGITransport, AsyncClient

    worker, _job = worker
    entered = asyncio.Event()

    async def loop():
        entered.set()
        await asyncio.Event().wait()

    worker.loop = loop
    supervisor = WorkerSupervisor(worker)
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            log_to_file=False,
            database_url=None,
            launch_signing_key=None,
        )
    )
    async with app.router.lifespan_context(app):
        app.state.database_status = "CONNECTED"
        app.state.database_pool = Pool()
        app.state.workflow = object()
        app.state.worker_supervisor = supervisor
        await supervisor.start()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health/ready")).status_code == 200
            supervisor.status = "RETRYING"
            response = await client.get("/health/ready")
            assert response.status_code == 503 and response.json()["worker"] == "RETRYING"
            assert (await client.get("/health/live")).status_code == 200
