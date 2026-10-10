"""ASGI request timing without reading bodies, headers or query parameters."""

import logging
from time import perf_counter
from uuid import uuid4

from chemo_agent_product.core.observability import context_id_var, emit, log_context

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started, status = perf_counter(), 500
        request_id = str(uuid4())  # Never trust a caller-supplied log correlation field.
        scope.setdefault("state", {})["request_id"] = request_id

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode("ascii")),
                    ],
                }
            await send(message)

        with log_context(request_id=request_id, context_id=None, job_id=None, agent_run_id=None):
            try:
                await self.app(scope, receive, observed_send)
            except Exception as exc:
                emit(logger, "http_unhandled", level=logging.ERROR, exc=exc)
                raise
            finally:
                route = getattr(scope.get("route"), "path", None)
                emit(
                    logger,
                    "http_completed",
                    method=scope.get("method"),
                    route=route,
                    context_id=scope.get("path_params", {}).get("context_id")
                    or context_id_var.get(),
                    http_status=status,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
