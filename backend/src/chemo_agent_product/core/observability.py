"""Single-line diagnostics using an allowlist, never arbitrary payloads or exception text."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import UUID

from chemo_agent_product.core.config import Settings

request_id_var = ContextVar("request_id", default=None)
context_id_var = ContextVar("context_id", default=None)
job_id_var = ContextVar("job_id", default=None)
agent_run_id_var = ContextVar("agent_run_id", default=None)

EVENTS = frozenset(
    {
        "logging_configured",
        "application_started",
        "application_stopped",
        "database_connection_failed",
        "http_completed",
        "http_unhandled",
        "database_error",
        "business_error",
        "worker_started",
        "worker_stopped",
        "worker_loop_failed",
        "worker_restart",
        "job_claimed",
        "job_finished",
        "job_failed",
        "job_cancelled",
        "job_abandoned",
        "job_dead_letter",
        "lease_renewed",
        "lease_lost",
        "heartbeat_failed",
        "agent_started",
        "agent_completed",
        "agent_failed",
        "model_started",
        "model_completed",
        "model_failed",
        "model_cancelled",
        "model_stderr",
        "model_stderr_summary",
        "external_log",
    }
)
PACKAGE = Path(__file__).resolve().parents[1]
MODULES = frozenset(
    "chemo_agent_product." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
    for path in PACKAGE.rglob("*.py")
)
# Only reviewed codes declared in this product may enter the log. Unknown
# BusinessError codes (including upstream text) are replaced, never echoed.
ERROR_CODES = frozenset(
    {
        "AGENT_CANDIDATE_INVALID",
        "AGENT_CONTEXT_STALE",
        "AGENT_FACT_SOURCE_INVALID",
        "AGENT_KIND_MISMATCH",
        "AGENT_NOT_CONFIGURED",
        "AGENT_PROFILE_CHANGED",
        "AGENT_QUOTE_INVALID",
        "AGENT_REFERENCE_INVALID",
        "AGENT_REVISION_NOT_EXPECTED",
        "AGENT_RUN_FORBIDDEN",
        "AGENT_SUMMARY_UNSOURCED",
        "APPLICABILITY_AMBIGUOUS",
        "AUTH_REQUIRED",
        "CALCULATION_POLICY_CONFLICT",
        "CANDIDATE_STALE",
        "CATALOG_UNAVAILABLE",
        "CHANGE_REASON_REQUIRED",
        "CLINICAL_AUTH_NOT_CONFIGURED",
        "CLINICAL_RELEASE_NOT_CONFIGURED",
        "COMMAND_IN_PROGRESS",
        "CONTEXT_EXPIRED",
        "CONTEXT_FORBIDDEN",
        "CYCLE_RANGE_INVALID",
        "DELIVERY_OPERATION_INVALID",
        "DELIVERY_OPERATOR_MISMATCH",
        "DELIVERY_OUTCOME_UNKNOWN",
        "DELIVERY_PAYLOAD_INVALID",
        "DELIVERY_RECEIPT_INVALID",
        "DELIVERY_RECORD_MISMATCH",
        "DOSE_INVALID",
        "EVIDENCE_NAMESPACE_INVALID",
        "FIELD_MAPPING_INVALID",
        "FIELD_NOT_EDITABLE",
        "FIELD_VALUE_INVALID",
        "FIXED_TEMPLATE_MISSING",
        "FIXED_VERSION_CHANGED",
        "FORBIDDEN",
        "GENERATION_CONFLICT",
        "HEARTBEAT_FAILED",
        "HOSPITAL_AUTH_NOT_CONFIGURED",
        "HOSPITAL_BUSINESS_RESPONSE_INVALID",
        "HOSPITAL_CREDENTIAL_NOT_CONFIGURED",
        "HOSPITAL_DELIVERY_NOT_CONFIGURED",
        "HOSPITAL_IDENTITY_NOT_CONFIGURED",
        "HOSPITAL_NOT_CONFIGURED",
        "HOSPITAL_ROUTE_INVALID",
        "HOSPITAL_ROUTE_NOT_CONFIGURED",
        "HOSPITAL_TIMEOUT",
        "HOST_AUTH_NOT_CONFIGURED",
        "IDEMPOTENCY_CONFLICT",
        "IDEMPOTENCY_KEY_REQUIRED",
        "IDENTITY_MAPPING_REQUIRED",
        "INSTANCE_FORBIDDEN",
        "ISSUES_NOT_ACKNOWLEDGED",
        "JOB_LEASE_LOST",
        "KNOWLEDGE_VERSION_CHANGED",
        "LAUNCH_SUPERSEDED",
        "LEASE_RECOVERY_EXHAUSTED",
        "MEDICATION_NOT_IN_TEMPLATE",
        "MODEL_NOT_CONFIGURED",
        "MODEL_OUTPUT_UNAVAILABLE",
        "MODEL_RUN_FAILED",
        "MODEL_TIMEOUT",
        "PATIENT_SCOPE_MISMATCH",
        "PREPARE_CONTEXT_REQUIRED",
        "PREPARE_NOT_READY",
        "REVIEWER_FACT_WRITE_FORBIDDEN",
        "REVIEW_REVISION_REQUIRED",
        "REVISION_CONFLICT",
        "REVISION_FORBIDDEN",
        "REVISION_HASH_MISMATCH",
        "RULE_PACKAGE_INVALID",
        "RULE_SCHEMA_UNSUPPORTED",
        "SAVED_REVISION_REQUIRED",
        "SNAPSHOT_CHANGED",
        "SNAPSHOT_NOT_READY",
        "SOURCE_PAGINATION_INCOMPLETE",
        "SOURCE_PATIENT_MISMATCH",
        "TASK_FAILED",
        "TEXT_MAPPING_INVALID",
        "TOOL_ARGUMENT_INVALID",
        "TOOL_DENIED",
        "TOOL_SCOPE_DENIED",
        "UNAUTHORIZED",
        "UNCLASSIFIED",
        "WORKFLOW_NOT_CONFIGURED",
        "X_CANDIDATE_NOT_SELECTABLE",
    }
)
ID_FIELDS = {"request_id", "context_id", "job_id", "agent_run_id", "prepare_run_id"}
NUMERIC_FIELDS = {
    "duration_ms",
    "cost_usd",
    "turns",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "http_status",
    "attempt",
    "restart_count",
    "retry_delay_seconds",
    "stderr_lines",
    "suppressed_lines",
}
ENUM_FIELDS = {
    "method": {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"},
    "job_kind": {"PREPARE", "AGENT_RUN"},
    "profile_kind": {"RECOMMENDATION", "REVIEWER"},
    "status": {
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "RUNNING",
        "QUEUED",
        "RETRY_WAIT",
        "DEAD_LETTER",
        "STARTING",
        "RETRYING",
        "STOPPED",
        "DISABLED",
        "NOT_CONFIGURED",
    },
    "diagnostic": {"AUTH", "RATE_LIMIT", "UPSTREAM", "UNCLASSIFIED"},
    "component": {"catalog", "context", "worker", "model"},
}
ROUTES = set()


@dataclass(frozen=True)
class ExceptionDiagnostic:
    types: tuple[str, ...]
    locations: tuple[str, ...]
    sqlstate: str | None


def register_routes(app):
    ROUTES.update(route.path for route in app.routes if hasattr(route, "path"))


def safe_id(value):
    if not isinstance(value, str | UUID):
        return None
    try:
        return str(UUID(str(value))) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def safe_fields(values):
    result = {}
    if not isinstance(values, dict):
        return result
    for key, value in values.items():
        if key in ID_FIELDS:
            if (identifier := safe_id(value)) is not None:
                result[key] = identifier
        elif key in NUMERIC_FIELDS:
            if type(value) in {int, float} and 0 <= value <= 10**15 and math.isfinite(value):
                result[key] = value
        elif key in ENUM_FIELDS:
            if isinstance(value, str) and value in ENUM_FIELDS[key]:
                result[key] = value
        elif key == "error_code":
            if value is not None:
                result[key] = (
                    value if isinstance(value, str) and value in ERROR_CODES else "UNCLASSIFIED"
                )
        elif key == "sqlstate":
            if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value):
                result[key] = value
        elif key == "file_enabled" and type(value) is bool:
            result[key] = value
        elif key == "route":
            # Caller supplies the matched route template, never an actual URL.
            if isinstance(value, str) and value in ROUTES:
                result[key] = value
    return result


def exception_fields(exc: BaseException):
    """Types and product filename/line only: no messages, locals, SQL, source or paths."""
    types, locations, seen = [], [], set()
    current = exc
    sqlstate = None
    while current is not None and id(current) not in seen and len(types) < 8:
        seen.add(id(current))
        name = type(current).__name__
        types.append(name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", name) else "Exception")
        sqlstate = sqlstate or getattr(current, "sqlstate", None)
        tb = current.__traceback__
        while tb is not None:
            try:
                relative = Path(tb.tb_frame.f_code.co_filename).resolve().relative_to(PACKAGE)
                locations.append(f"{relative.as_posix()}:{tb.tb_lineno}")
            except (ValueError, OSError):
                pass
            tb = tb.tb_next
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return ExceptionDiagnostic(tuple(types), tuple(locations[-12:]), sqlstate)


@contextmanager
def log_context(**values):
    variables = {
        "request_id": request_id_var,
        "context_id": context_id_var,
        "job_id": job_id_var,
        "agent_run_id": agent_run_id_var,
    }
    tokens = [(variables[key], variables[key].set(safe_id(value))) for key, value in values.items()]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def emit(logger, event, *, level=logging.INFO, exc=None, **values):
    event = event if isinstance(event, str) and event in EVENTS else "external_log"
    values = {
        "request_id": request_id_var.get(),
        "context_id": context_id_var.get(),
        "job_id": job_id_var.get(),
        "agent_run_id": agent_run_id_var.get(),
        **values,
    }
    logger.log(
        level,
        event,
        extra={
            "safe_event": event,
            "safe_fields": safe_fields(values),
            "safe_exception": exception_fields(exc) if exc else None,
        },
    )


class PrivacyFilter(logging.Filter):
    def filter(self, record):
        event = getattr(record, "safe_event", "external_log")
        supplied = getattr(record, "safe_fields", {})
        fields = safe_fields(
            {
                "request_id": request_id_var.get(),
                "context_id": context_id_var.get(),
                "job_id": job_id_var.get(),
                "agent_run_id": agent_run_id_var.get(),
                **(supplied if isinstance(supplied, dict) else {}),
            }
        )
        if not isinstance(event, str) or event not in EVENTS:
            event = "external_log"
        diagnostic = getattr(record, "safe_exception", None)
        if record.exc_info and record.exc_info[1]:
            diagnostic = exception_fields(record.exc_info[1])
        # Mutate before ANY sink, including pre-existing handlers. This also
        # removes SDK/uvicorn request URLs, exception text and formatter caches.
        record.msg, record.args = event, ()
        record.exc_info = record.exc_text = record.stack_info = None
        record.safe_event, record.safe_fields = event, safe_fields(fields)
        record.safe_exception = diagnostic if isinstance(diagnostic, ExceptionDiagnostic) else None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        PrivacyFilter().filter(record)
        module = record.name if record.name in MODULES else "external"
        diagnostic = record.safe_exception
        detail = (
            {
                "exception_types": diagnostic.types,
                "code_locations": diagnostic.locations,
                **safe_fields({"sqlstate": diagnostic.sqlstate}),
            }
            if diagnostic
            else {}
        )
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "module": module,
                "event": record.safe_event,
                **record.safe_fields,
                **detail,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def configure_logging(settings: Settings):
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "product_sink", False):
            root.removeHandler(handler)
            handler.close()
    handlers = [logging.StreamHandler(sys.stdout)]
    if settings.log_to_file and settings.log_directory is not None:
        settings.log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                settings.log_directory / f"{settings.environment}.jsonl",
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.product_sink = True
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    # Cover existing framework/SDK handlers as well; no raw exception/access logs.
    for logger in [
        root,
        *[
            item
            for item in logging.Logger.manager.loggerDict.values()
            if isinstance(item, logging.Logger)
        ],
    ]:
        for handler in logger.handlers:
            if not any(isinstance(f, PrivacyFilter) for f in handler.filters):
                handler.addFilter(PrivacyFilter())
            handler.setFormatter(JsonFormatter())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    root.setLevel(settings.log_level)
    emit(
        logging.getLogger(__name__),
        "logging_configured",
        file_enabled=bool(settings.log_to_file and settings.log_directory),
    )
