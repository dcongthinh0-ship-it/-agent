"""Privacy checks across file/console/framework/HTTP/SDK sinks, using synthetic sentinels."""

import asyncio
import json
import logging
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.observability import configure_logging, emit, log_context
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.entrypoints.api import create_app
from chemo_agent_product.integrations.model.claude import ClaudeAdapter

SENSITIVE = "张敏隐私标记_SECRET_SENTINEL_987654"
logger = logging.getLogger("chemo_agent_product.core.observability")


@pytest.fixture
def logs(tmp_path):
    config = Settings(
        _env_file=None,
        environment="test",
        log_directory=tmp_path,
        database_url=None,
        launch_signing_key=None,
    )
    configure_logging(config)
    yield (
        config,
        lambda: [json.loads(line) for line in (tmp_path / "test.jsonl").read_text().splitlines()],
    )
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "product_sink", False):
            root.removeHandler(handler)
            handler.close()


def test_free_text_exception_chain_and_unknown_fields_never_reach_sinks(logs, capsys):
    _, read = logs
    context_id = uuid4()
    try:
        try:
            raise ValueError(SENSITIVE)
        except ValueError as cause:
            raise asyncpg.UniqueViolationError(SENSITIVE) from cause
    except Exception as exc:
        with log_context(context_id=context_id):
            emit(
                logger,
                "database_error",
                exc=exc,
                error_code="CATALOG_UNAVAILABLE",
                payload={"patient": SENSITIVE},
                token=SENSITIVE,
                message=SENSITIVE,
                exception_types=[SENSITIVE],
                code_locations=[SENSITIVE],
                duration_ms=12.5,
            )
        logging.getLogger("uvicorn.error").exception("request token=%s", SENSITIVE)
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', SENSITIVE, "GET", f"/{SENSITIVE}", "1.1", 500
        )
    lines = read()
    row = next(line for line in lines if line["event"] == "database_error")
    assert row["context_id"] == str(context_id)
    assert row["sqlstate"] == "23505"
    assert row["exception_types"] == ["UniqueViolationError", "ValueError"]
    assert row["duration_ms"] == 12.5
    assert "token" not in row and "payload" not in row
    assert SENSITIVE not in json.dumps(lines, ensure_ascii=False)
    assert SENSITIVE not in capsys.readouterr().out


def test_unknown_codes_bad_ids_nonfinite_metrics_and_dynamic_logger_names_are_dropped(logs):
    _, read = logs
    emit(
        logging.getLogger(f"chemo_agent_product.{SENSITIVE}"),
        "model_completed",
        error_code=SENSITIVE,
        request_id=SENSITIVE,
        turns=float("nan"),
        cost_usd=-1,
        input_tokens=10**1000,
        route=f"/api/v1/{SENSITIVE}",
    )
    row = read()[-1]
    assert row["module"] == "external" and row["error_code"] == "UNCLASSIFIED"
    assert not {"request_id", "turns", "cost_usd", "route", "input_tokens"} & row.keys()
    assert SENSITIVE not in json.dumps(row, ensure_ascii=False)


def test_malformed_external_log_metadata_cannot_bypass_filter_or_crash_logging(logs):
    _, read = logs
    logging.getLogger("external-sdk").error(
        SENSITIVE, extra={"safe_event": {"patient": SENSITIVE}, "safe_fields": [SENSITIVE]}
    )
    assert read()[-1]["event"] == "external_log"
    assert SENSITIVE not in json.dumps(read(), ensure_ascii=False)


def test_reconfiguration_does_not_duplicate_sinks_and_rotates_files(logs):
    config, _ = logs
    config = config.model_copy(update={"log_max_bytes": 1024, "log_backup_count": 2})
    configure_logging(config)
    configure_logging(config)
    assert sum(getattr(h, "product_sink", False) for h in logging.getLogger().handlers) == 2
    for _ in range(35):
        emit(logger, "http_completed", http_status=200, duration_ms=1)
    files = list(config.log_directory.glob("test.jsonl*"))
    assert len(files) == 3
    assert all(json.loads(line) for file in files for line in file.read_text().splitlines())


@pytest.mark.asyncio
async def test_http_logs_only_templates_and_generated_ids_even_on_validation_and_failure(logs):
    config, read = logs
    app = create_app(config)

    @app.post("/api/v1/privacy-check")
    async def broken():
        raise RuntimeError(SENSITIVE)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            result = await client.post(
                f"/api/v1/privacy-check?token={SENSITIVE}",
                headers={
                    "Authorization": "Bearer SECRET_SENTINEL_987654",
                    "X-Request-ID": "SECRET_SENTINEL_987654",
                },
                json={"patient_name": SENSITIVE},
            )
            invalid = await client.get(f"/api/v1/contexts/{SENSITIVE}")
            unknown = await client.get(f"/unknown/{SENSITIVE}")
            ready = await client.get("/health/ready")
    assert result.status_code == 500 and result.json()["code"] == "INTERNAL_ERROR"
    UUID(result.headers["X-Request-ID"])
    assert SENSITIVE not in result.text
    assert invalid.status_code == 422 and unknown.status_code == 404
    assert ready.status_code == 503
    rows = read()
    assert SENSITIVE not in json.dumps(rows, ensure_ascii=False)
    assert "SECRET_SENTINEL_987654" not in json.dumps(rows)
    completed = [row for row in rows if row["event"] == "http_completed"]
    assert completed[0]["request_id"] == result.headers["X-Request-ID"]
    assert completed[0]["route"] == "/api/v1/privacy-check"
    assert "route" not in next(row for row in completed if row["http_status"] == 404)


@pytest.mark.asyncio
async def test_business_and_database_failures_have_safe_codes_and_different_severity(logs):
    config, read = logs
    app = create_app(config)

    @app.get("/api/v1/log-conflict")
    async def conflict():
        raise BusinessError("REVISION_CONFLICT", "版本冲突", 409)

    @app.get("/api/v1/log-database")
    async def database():
        raise asyncpg.UniqueViolationError(SENSITIVE)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/log-conflict")).status_code == 409
            assert (await client.get("/api/v1/log-database")).status_code == 503
    business = next(row for row in read() if row["event"] == "business_error")
    database_log = next(row for row in read() if row["event"] == "database_error")
    assert business["level"] == "WARNING" and business["error_code"] == "REVISION_CONFLICT"
    assert database_log["level"] == "ERROR" and database_log["sqlstate"] == "23505"
    assert SENSITIVE not in json.dumps(read(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_startup_database_failure_is_logged_safely(logs, monkeypatch):
    config, read = logs

    async def fail(_dsn):
        raise OSError(SENSITIVE)

    monkeypatch.setattr("chemo_agent_product.entrypoints.api.open_pool", fail)
    app = create_app(config.model_copy(update={"database_url": SecretStr(SENSITIVE)}))
    async with app.router.lifespan_context(app):
        assert app.state.database_status == "UNAVAILABLE"
    assert any(row["event"] == "database_connection_failed" for row in read())
    assert SENSITIVE not in json.dumps(read(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_readiness_probes_live_database_instead_of_trusting_startup_state(logs):
    from unittest.mock import AsyncMock

    config, read = logs
    app = create_app(config)
    async with app.router.lifespan_context(app):
        app.state.database_status = "CONNECTED"
        app.state.database_pool = AsyncMock()
        app.state.database_pool.fetchval.side_effect = asyncpg.ConnectionDoesNotExistError(
            SENSITIVE
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health/ready")).status_code == 503
            assert (await client.get("/health/live")).status_code == 200
    assert SENSITIVE not in json.dumps(read(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_concurrent_requests_keep_distinct_correlation_ids(logs):
    config, read = logs
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            results = await asyncio.gather(*(client.get("/health/live") for _ in range(8)))
    ids = {result.headers["X-Request-ID"] for result in results}
    assert len(ids) == 8
    assert {row["request_id"] for row in read() if row["event"] == "http_completed"} == ids


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_sdk_stderr_is_bounded_and_metrics_are_logged_on_success_and_failure(
    logs, monkeypatch, failed
):
    from claude_agent_sdk import ResultMessage

    config, read = logs
    config = config.model_copy(
        update={
            "model_enabled": True,
            "model_api_key": SecretStr(SENSITIVE),
            "model_name": "CONTRACT_ONLY",
        }
    )

    class Client:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def query(self, _input):
            for _ in range(25):
                self.options.stderr(f"API Error: 401 {SENSITIVE}")

        async def receive_response(self):
            yield ResultMessage(
                subtype="success",
                duration_ms=12,
                duration_api_ms=10,
                is_error=failed,
                num_turns=2,
                session_id=SENSITIVE,
                total_cost_usd=0.01,
                usage={"input_tokens": 10, "output_tokens": 4, "patient": SENSITIVE},
                result=SENSITIVE,
                errors=[SENSITIVE],
                structured_output=None if failed else {"ok": True},
            )

    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", Client)
    adapter = ClaudeAdapter(config)
    if failed:
        with pytest.raises(BusinessError, match="MODEL_OUTPUT_UNAVAILABLE"):
            await adapter.run("RECOMMENDATION", {"patient": SENSITIVE}, None)
    else:
        payload, usage = await adapter.run("RECOMMENDATION", {"patient": SENSITIVE}, None)
        assert payload == {"ok": True} and usage["cost_usd"] == 0.01
    rows = read()
    assert len([row for row in rows if row["event"] == "model_stderr"]) == 3
    summary = next(row for row in rows if row["event"] == "model_stderr_summary")
    assert summary["stderr_lines"] == 25 and summary["suppressed_lines"] == 22
    result = next(row for row in rows if row["event"] == "model_completed")
    assert result["cost_usd"] == 0.01 and result["input_tokens"] == 10
    assert result["status"] == ("FAILED" if failed else "SUCCEEDED")
    assert SENSITIVE not in json.dumps(rows, ensure_ascii=False)


@pytest.mark.asyncio
async def test_model_cancellation_has_duration_without_failure_text_or_success_event(logs):
    from unittest.mock import AsyncMock

    config, read = logs
    adapter = ClaudeAdapter(config)
    adapter.receive = AsyncMock(side_effect=asyncio.CancelledError(SENSITIVE))
    with pytest.raises(asyncio.CancelledError):
        await adapter.run("RECOMMENDATION", {"patient": SENSITIVE}, None)
    row = next(row for row in read() if row["event"] == "model_cancelled")
    assert row["duration_ms"] >= 0
    assert not any(row["event"] == "model_completed" for row in read())
    assert SENSITIVE not in json.dumps(read(), ensure_ascii=False)
