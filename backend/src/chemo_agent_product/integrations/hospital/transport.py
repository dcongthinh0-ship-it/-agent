"""HTTP transport with scoped attempt auditing. Credentials never enter audit payloads."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from chemo_agent_product.core.domain import fingerprint
from chemo_agent_product.core.security import BusinessError


async def call_hospital(
    client, profile, name, config, body, operator, observer=None, group=None, attempt=1
):
    credential = os.environ.get(profile.credential_environment_variable or "")
    if not credential:
        raise BusinessError("HOSPITAL_CREDENTIAL_NOT_CONFIGURED", "院方认证尚未配置", 503)
    if not config.url:
        raise BusinessError("HOSPITAL_ROUTE_NOT_CONFIGURED", "院方路由待配置", 503)
    url = httpx.URL(config.url)
    if url.scheme not in {"http", "https"} or url.userinfo:
        raise BusinessError("HOSPITAL_ROUTE_INVALID", "院方路由配置不正确", 503)
    request_id = str(uuid4())
    headers = {
        "Request-Id": request_id,
        "Timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d%H%M%S%f")[:-3],
        "Authorization": credential,
        "Operator-Id": operator,
        "Content-Type": "application/json;charset=utf-8",
    }
    if profile.hospital_code:
        headers["Hospital-Code"] = profile.hospital_code
    event = {
        "request_id": request_id,
        "call_group_id": str(group or uuid4()),
        "attempt_no": attempt,
        "operation_name": name,
        "contract_version": profile.contract_version,
        "request_hash": fingerprint(body),
        "started_at": datetime.now(UTC),
    }
    attempt_ref = await observer({**event, "phase": "STARTED"}) if observer else None
    try:
        response = await client.request(config.method, config.url, json=body, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        if observer:
            await observer(
                {
                    **event,
                    "phase": "COMPLETED",
                    "completed_at": datetime.now(UTC),
                    "transport_outcome": "TIMEOUT"
                    if isinstance(exc, httpx.TimeoutException)
                    else "NETWORK_ERROR",
                    "safe_error_summary": "院方调用超时"
                    if isinstance(exc, httpx.TimeoutException)
                    else "院方网络连接失败",
                }
            )
        raise
    try:
        data = response.json()
    except ValueError:
        data = None
    if observer:
        await observer(
            {
                **event,
                "phase": "COMPLETED",
                "completed_at": datetime.now(UTC),
                "transport_outcome": "RESPONDED",
                "http_status": response.status_code,
                "business_code": str(data.get("code")) if isinstance(data, dict) else None,
                "response_hash": hashlib.sha256(response.content).hexdigest(),
            }
        )
    response.raise_for_status()
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        raise BusinessError("HOSPITAL_BUSINESS_RESPONSE_INVALID", "院方未返回有效成功响应", 502)
    return data, attempt_ref
