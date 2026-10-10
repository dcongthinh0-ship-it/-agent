"""Local/test host authentication. Never accepted as production hospital SSO."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Literal
from uuid import UUID

from pydantic import Field

from chemo_agent_product.core.domain import Contract


class BusinessError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


class Principal(Contract):
    subject: str = Field(min_length=1)
    hospital_id: UUID
    staff_id: UUID
    roles: list[Literal["DOCTOR", "KNOWLEDGE_REVIEWER", "OPERATOR"]]
    patient_id: UUID | None = None
    encounter_id: UUID | None = None
    usage_mode: Literal["TEST_ONLY"] = "TEST_ONLY"
    issuer: Literal["chemo-local-host.v1"] = "chemo-local-host.v1"
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def issue_test_token(principal: Principal, key: str) -> str:
    if len(key) < 32:
        raise ValueError("signing key must have at least 32 characters")
    body = _encode(principal.model_dump_json().encode())
    signature = _encode(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_test_token(token: str, key: str | None, environment: str) -> Principal:
    if environment not in {"local", "test"}:
        raise BusinessError("HOSPITAL_AUTH_NOT_CONFIGURED", "院方身份验证尚未配置", 503)
    if not key or len(key) < 32:
        raise BusinessError("HOST_AUTH_NOT_CONFIGURED", "工作站身份合同尚未配置", 503)
    try:
        if len(token) > 8192:
            raise ValueError("oversized")
        body, signature = token.split(".")
        expected = _encode(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        principal = Principal.model_validate(
            json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        )
        if principal.expires_at <= time.time():
            raise ValueError("expired")
        return principal
    except (ValueError, TypeError) as exc:
        raise BusinessError("UNAUTHORIZED", "身份凭据无效或已过期", 401) from exc


def require_role(principal: Principal, role: str) -> None:
    if role not in principal.roles:
        raise BusinessError("FORBIDDEN", "当前身份无此操作权限", 403)
