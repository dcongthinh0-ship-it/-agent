"""Own the worker task, retry loop failures with bounded backoff, and expose readiness."""

import asyncio
import logging
from time import monotonic

from chemo_agent_product.core.observability import emit

logger = logging.getLogger(__name__)


class WorkerSupervisor:
    def __init__(self, worker):
        self.worker = worker
        self.task = None
        self.status = "STOPPED"
        self.restart_count = 0

    async def start(self):
        self.status = "STARTING"
        self.task = asyncio.create_task(self.run(), name="product-worker-supervisor")
        await asyncio.sleep(0)

    @property
    def healthy(self):
        return bool(self.status == "RUNNING" and self.task and not self.task.done())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        consecutive = 0
        try:
            while True:
                self.status = "RUNNING"
                started = monotonic()
                emit(logger, "worker_started", restart_count=self.restart_count)
                try:
                    await self.worker.loop()
                    raise RuntimeError("Worker loop returned unexpectedly")
                except asyncio.CancelledError as exc:
                    if asyncio.current_task().cancelling():
                        raise
                    failure = exc
                except Exception as exc:
                    failure = exc
                self.status = "RETRYING"
                consecutive = 0 if monotonic() - started >= 60 else consecutive
                consecutive += 1
                self.restart_count += 1
                delay = min(
                    2 ** min(consecutive - 1, 10),
                    self.worker.settings.worker_restart_max_seconds,
                )
                emit(
                    logger,
                    "worker_loop_failed",
                    level=logging.ERROR,
                    exc=failure,
                    restart_count=self.restart_count,
                    retry_delay_seconds=delay,
                )
                await asyncio.sleep(delay)
                emit(logger, "worker_restart", restart_count=self.restart_count)
        finally:
            self.status = "STOPPED"
            emit(logger, "worker_stopped", restart_count=self.restart_count)
