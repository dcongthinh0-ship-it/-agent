"""Final v1.0.1 adapter. URLs, credentials and field/dictionary mapping remain external config."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import Field

from chemo_agent_product.domain import Contract, Fact, PatientSnapshot, Reference, fingerprint
from chemo_agent_product.security import BusinessError

READ_OPERATIONS = frozenset(
    {
        "Q_GetPatientClinicalData",
        "Q_GetPatientEncounter",
        "Q_GetClinicalRecord",
        "Q_GetClinicalReport",
        "Q_GetDrugOrdersHistory",
        "Q_GetOrderDictionary",
        "Q_GetMedicalStaffInfo",
        "Q_GetPatientOperationHistory",
    }
)


class OperationConfig(Contract):
    url: str | None = None
    method: Literal["POST"] = "POST"
    request_defaults: dict[str, Any] = Field(default_factory=dict)
    response_pointer: str = "/data/content"
    patient_pointer: str | None = None
    encounter_pointer: str | None = None
    identity_policy: Literal["RESPONSE", "REQUEST_SCOPE"] = "RESPONSE"
    required: bool = True
    max_pages: int = Field(default=10, ge=1, le=100)


class FactMap(Contract):
    operation: str
    pointer: str
    unit: str | None = None
    unit_pointer: str | None = None
    observed_at_pointer: str | None = None
    status_pointer: str | None = None
    status_map: dict[str, str] = Field(default_factory=dict)
    value_map: dict[str, str] = Field(default_factory=dict)


class TextMap(Contract):
    operation: str
    records_pointer: str
    text_pointer: str
    record_id_pointer: str | None = None
    category: str = Field(min_length=1, max_length=100)


class AdapterProfile(Contract):
    schema_version: Literal["hospital-adapter.v1"] = "hospital-adapter.v1"
    contract_version: str
    credential_environment_variable: str | None = None
    hospital_code: str | None = None
    operations: dict[str, OperationConfig]
    facts: dict[str, FactMap] = Field(default_factory=dict)
    text_records: list[TextMap] = Field(default_factory=list)
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class SourcePayload(Contract):
    operation: str
    source_key: str
    received_at: datetime
    identity_state: str
    payload: dict[str, Any]
    content_hash: str


class HospitalReader(Protocol):
    async def fetch(self, patient: str, encounter: str, operator: str) -> list[SourcePayload]: ...
    def normalize(
        self, sources: list[SourcePayload], patient: str, encounter: str
    ) -> PatientSnapshot: ...


def pointer(payload: Any, path: str) -> Any:
    if path == "":
        return payload
    if not path.startswith("/"):
        raise ValueError("JSON pointer must be absolute")
    current = payload
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            current = current[int(key)] if isinstance(current, list) else current[key]
        return current
    except (KeyError, IndexError, TypeError, ValueError):
        return None


class ConfiguredHospitalReader:
    def __init__(self, path: str | None, transport: httpx.AsyncBaseTransport | None = None):
        self.profile = AdapterProfile.model_validate_json(Path(path).read_text()) if path else None
        self.transport = transport
        if self.profile and any(op not in READ_OPERATIONS for op in self.profile.operations):
            raise ValueError("unknown hospital read operation; configure verified contract names")
        if self.profile and any(
            mapping.operation not in self.profile.operations
            for mapping in [*self.profile.facts.values(), *self.profile.text_records]
        ):
            raise ValueError("field or text mapping points to an unconfigured operation")

    async def fetch(self, patient: str, encounter: str, operator: str) -> list[SourcePayload]:
        profile = self.profile
        if not profile or not profile.operations:
            raise BusinessError("HOSPITAL_NOT_CONFIGURED", "院方读取服务尚未配置", 503)
        credential = os.environ.get(profile.credential_environment_variable or "")
        if not credential:
            raise BusinessError("HOSPITAL_CREDENTIAL_NOT_CONFIGURED", "院方认证尚未配置", 503)
        async with httpx.AsyncClient(
            timeout=profile.timeout_seconds, transport=self.transport, follow_redirects=False
        ) as client:

            async def read(name: str, config: OperationConfig):
                if not config.url:
                    if config.required:
                        raise BusinessError("HOSPITAL_ROUTE_NOT_CONFIGURED", "院方路由待配置", 503)
                    return None
                u = httpx.URL(config.url)
                if u.scheme not in {"http", "https"} or u.userinfo:
                    raise BusinessError("HOSPITAL_ROUTE_INVALID", "院方路由配置不正确", 503)
                records = []
                for page in range(1, config.max_pages + 1):
                    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d%H%M%S%f")[:-3]
                    body = {
                        **config.request_defaults,
                        "patient_id": patient,
                        "encounter_id": encounter,
                    }
                    if "page" in body:
                        body["page"] = page
                    headers = {
                        "Request-Id": str(uuid4()),
                        "Timestamp": stamp,
                        "Authorization": credential,
                        "Operator-Id": operator,
                        "Content-Type": "application/json;charset=utf-8",
                    }
                    if profile.hospital_code:
                        headers["Hospital-Code"] = profile.hospital_code
                    response = await client.post(config.url, json=body, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict) or str(data.get("code")) != "0":
                        raise BusinessError("HOSPITAL_READ_FAILED", "院方未返回成功读取状态", 502)
                    if config.identity_policy == "RESPONSE":
                        if not config.patient_pointer or not config.encounter_pointer:
                            raise BusinessError(
                                "IDENTITY_MAPPING_REQUIRED", "返回身份定位字段尚未配置", 503
                            )
                        if (
                            pointer(data, config.patient_pointer) != patient
                            or pointer(data, config.encounter_pointer) != encounter
                        ):
                            raise BusinessError(
                                "SOURCE_PATIENT_MISMATCH", "返回数据与当前患者或就诊不一致", 409
                            )
                    now = datetime.now(UTC)
                    records.append(
                        SourcePayload(
                            operation=name,
                            source_key=f"{encounter}:{name}:{page}",
                            received_at=now,
                            identity_state="VERIFIED_RESPONSE"
                            if config.identity_policy == "RESPONSE"
                            else "CONFIGURED_REQUEST_SCOPE",
                            payload=data,
                            content_hash=fingerprint(data),
                        )
                    )
                    total = pointer(data, "/data/total")
                    content = pointer(data, config.response_pointer)
                    size = body.get("size")
                    if (
                        "page" not in body
                        or not isinstance(total, int)
                        or not isinstance(size, int)
                        or page * size >= total
                    ):
                        break
                    if page == config.max_pages:
                        raise BusinessError(
                            "SOURCE_PAGINATION_INCOMPLETE", "返回数据超过配置的完整读取上限", 502
                        )
                    if not isinstance(content, list) or not content:
                        raise BusinessError("SOURCE_PAGINATION_INCOMPLETE", "分页结果不完整", 502)
                return records

            results = await asyncio.gather(*(read(n, c) for n, c in profile.operations.items()))
        return [record for group in results if group for record in group]

    def normalize(
        self, sources: list[SourcePayload], patient: str, encounter: str
    ) -> PatientSnapshot:
        if not self.profile:
            raise BusinessError("HOSPITAL_NOT_CONFIGURED", "院方读取服务尚未配置", 503)
        facts = {}
        for code, mapping in self.profile.facts.items():
            matches = [s for s in sources if s.operation == mapping.operation]
            extracted = []
            for source in matches:
                value = pointer(source.payload, mapping.pointer)
                if value is None:
                    continue
                if not isinstance(value, (str, int, float, bool)):
                    raise BusinessError("FIELD_MAPPING_INVALID", "患者字段映射未返回标量值", 503)
                unit = (
                    pointer(source.payload, mapping.unit_pointer)
                    if mapping.unit_pointer
                    else mapping.unit
                )
                observed = (
                    pointer(source.payload, mapping.observed_at_pointer)
                    if mapping.observed_at_pointer
                    else None
                )
                observed_at = None
                if observed:
                    try:
                        if len(str(observed)) == 17 and str(observed).isdigit():
                            observed_at = datetime.strptime(
                                str(observed), "%Y%m%d%H%M%S%f"
                            ).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                        else:
                            observed_at = datetime.fromisoformat(
                                str(observed).replace("Z", "+00:00")
                            )
                        if observed_at.tzinfo is None:
                            observed_at = None
                    except ValueError:
                        pass
                raw_status = (
                    pointer(source.payload, mapping.status_pointer)
                    if mapping.status_pointer
                    else None
                )
                status = (
                    mapping.status_map.get(str(raw_status), "UNCERTAIN")
                    if mapping.status_pointer
                    else "CONFIRMED"
                )
                extracted.append(
                    Fact(
                        code=code,
                        value=mapping.value_map.get(str(value), value),
                        unit=unit,
                        observed_at=observed_at,
                        status=status,
                        source=Reference(
                            namespace=f"hospital:{mapping.operation}",
                            id=source.source_key,
                            version=self.profile.contract_version,
                            content_hash=source.content_hash,
                        ),
                        locator={
                            "json_pointer": mapping.pointer,
                            "received_at": source.received_at.isoformat(),
                        },
                    )
                )
            if extracted:
                current = extracted[0]
                if any(
                    (f.value, f.unit, f.status) != (current.value, current.unit, current.status)
                    for f in extracted[1:]
                ):
                    current = current.model_copy(update={"status": "CONFLICT"})
                facts[code] = current
        texts = []
        for mapping in self.profile.text_records:
            for source in sources:
                if source.operation != mapping.operation:
                    continue
                records = pointer(source.payload, mapping.records_pointer)
                if records is None:
                    continue
                if not isinstance(records, list):
                    raise BusinessError("TEXT_MAPPING_INVALID", "文本列表定位字段尚未正确配置", 503)
                for index, record in enumerate(records):
                    content = pointer(record, mapping.text_pointer)
                    if content is None:
                        continue
                    if not isinstance(content, str):
                        raise BusinessError(
                            "TEXT_MAPPING_INVALID", "病历文本定位字段未返回文本", 503
                        )
                    if not content.strip():
                        continue
                    external_id = (
                        pointer(record, mapping.record_id_pointer)
                        if mapping.record_id_pointer
                        else None
                    )
                    locator = f"{mapping.records_pointer}/{index}{mapping.text_pointer}"
                    texts.append(
                        {
                            "record_id": fingerprint(
                                [source.source_key, locator, external_id, content]
                            ),
                            "external_record_id": external_id,
                            "category": mapping.category,
                            "text": content,
                            "locator": {"json_pointer": locator},
                            "source": Reference(
                                namespace=f"hospital:{source.operation}",
                                id=source.source_key,
                                version=self.profile.contract_version,
                                content_hash=source.content_hash,
                            ).model_dump(mode="json"),
                        }
                    )
        return PatientSnapshot(
            patient_ref=patient,
            encounter_ref=encounter,
            captured_at=datetime.now(UTC),
            facts=facts,
            text_records=texts,
        )
