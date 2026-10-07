"""Typed transport foundation. No public write endpoint or automatic submission.

The workflow must authorize a frozen clinical revision before invoking this driver.
An HTTP timeout is an unknown external outcome, never permission to resubmit a write.
"""

from pathlib import Path

import httpx
from pydantic import Field, ValidationError

from chemo_agent_product.domain import Contract
from chemo_agent_product.hospital import OperationConfig, pointer
from chemo_agent_product.hospital_contracts import REQUEST_CONTRACTS, RESPONSE_CONTRACTS
from chemo_agent_product.hospital_transport import call_hospital
from chemo_agent_product.security import BusinessError


class DeliveryProfile(Contract):
    contract_version: str = Field(min_length=1)
    credential_environment_variable: str | None = None
    hospital_code: str | None = None
    operations: dict[str, OperationConfig] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class ConfiguredHospitalDelivery:
    def __init__(self, path: str | None, transport: httpx.AsyncBaseTransport | None = None):
        self.profile = DeliveryProfile.model_validate_json(Path(path).read_text()) if path else None
        self.transport = transport
        if self.profile and any(op not in REQUEST_CONTRACTS for op in self.profile.operations):
            raise ValueError("unknown hospital delivery operation")
        if self.profile and any(op.request_defaults for op in self.profile.operations.values()):
            raise ValueError("delivery payloads cannot be modified by route defaults")

    async def invoke(self, name: str, payload: dict, operator: str, *, observer, group=None):
        if name not in REQUEST_CONTRACTS:
            raise BusinessError("DELIVERY_OPERATION_INVALID", "不是已核对的院方交付操作", 400)
        if not self.profile or name not in self.profile.operations:
            raise BusinessError("HOSPITAL_DELIVERY_NOT_CONFIGURED", "院方交付操作尚未配置", 503)
        try:
            request = REQUEST_CONTRACTS[name].model_validate(payload)
        except ValidationError as exc:
            raise BusinessError(
                "DELIVERY_PAYLOAD_INVALID", "交付载荷不符合最终接口字段合同", 422
            ) from exc
        if getattr(request, "doctor_id", operator) != operator:
            raise BusinessError("DELIVERY_OPERATOR_MISMATCH", "调用医师与确认载荷不一致", 403)
        config = self.profile.operations[name]
        async with httpx.AsyncClient(
            timeout=self.profile.timeout_seconds, transport=self.transport, follow_redirects=False
        ) as client:
            try:
                envelope, attempt = await call_hospital(
                    client,
                    self.profile,
                    name,
                    config,
                    request.model_dump(mode="json", exclude_none=True),
                    operator,
                    observer,
                    group,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                # Do not retry here: a sent write may already have reached the hospital.
                raise BusinessError(
                    "DELIVERY_OUTCOME_UNKNOWN",
                    "未取得院方结果，须按原记录回查，不能重发新批次",
                    504,
                ) from exc
        response = pointer(envelope, config.response_pointer)
        if isinstance(response, list):
            response = response[0] if len(response) == 1 else None
        try:
            result = RESPONSE_CONTRACTS[name].model_validate(response)
        except ValidationError as exc:
            raise BusinessError(
                "DELIVERY_RECEIPT_INVALID", "院方返回不符合已核对的结果合同", 502
            ) from exc
        if name == "Q_GetRegimenArchiveStatus" and (
            result.patient_regimen_record_id != request.patient_regimen_record_id
            or (request.document_id and result.document_id != request.document_id)
        ):
            raise BusinessError("DELIVERY_RECORD_MISMATCH", "回查结果属于其他方案记录或文书", 409)
        return result, attempt
