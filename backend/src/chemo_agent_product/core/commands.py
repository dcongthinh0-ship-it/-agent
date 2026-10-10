from __future__ import annotations

from datetime import UTC, datetime

from chemo_agent_product.core import command_repository as repository
from chemo_agent_product.core.domain import fingerprint
from chemo_agent_product.core.security import BusinessError


class CommandService:
    def __init__(self, workflow):
        self.workflow = workflow

    async def command(self, c, p, operation, key, payload):
        if not key or len(key) > 128:
            raise BusinessError("IDEMPOTENCY_KEY_REQUIRED", "缺少有效操作标识", 422)
        row = await repository.reserve_command(
            c, p.subject, p.hospital_id, operation, key, fingerprint(payload)
        )
        if row:
            return row["id"], None
        old = await repository.get_command(c, p.hospital_id, p.subject, operation, key)
        if old["request_hash"] != fingerprint(payload):
            raise BusinessError("IDEMPOTENCY_CONFLICT", "同一操作标识已用于不同内容", 409)
        if old["status"] == "COMPLETED":
            return old["id"], old["response_summary"]
        raise BusinessError("COMMAND_IN_PROGRESS", "该操作正在处理，请按原操作标识重试", 409)

    async def complete(self, c, command_id, response, resource_type, resource_id):
        await repository.complete_command(c, command_id, response, resource_type, resource_id)

    async def audit(self, c, p, context, event, aggregate_id, summary):
        await repository.insert_audit_event(
            c,
            dict(
                created_by_principal=p.subject,
                event_type=event,
                actor_principal_ref=p.subject,
                hospital_id=p.hospital_id,
                aggregate_type="patient_flow",
                aggregate_id=aggregate_id,
                correlation_id=context["id"],
                event_time=datetime.now(UTC),
                patient_reference_id=context["patient_reference_id"],
                encounter_reference_id=context["encounter_reference_id"],
                safe_summary=summary,
                content_hash=fingerprint(summary),
            ),
        )
