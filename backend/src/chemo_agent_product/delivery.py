"""Readiness of a frozen, confirmed revision. This is not an HIS validation receipt."""

from __future__ import annotations

from uuid import UUID

from chemo_agent_product.security import BusinessError


async def readiness(workflow, p, context_id: UUID, instance_id: UUID):
    async with workflow.pool.acquire() as c, c.transaction(readonly=True):
        context, instance = await workflow.instance(c, context_id, p, instance_id)
        revision = (
            await c.fetchrow(
                "SELECT * FROM clinical.patient_regimen_revision WHERE id=$1",
                instance["current_revision_id"],
            )
            if instance["current_revision_id"]
            else None
        )
        if not revision:
            raise BusinessError("SAVED_REVISION_REQUIRED", "请先保存本次方案", 409)
        orders = await c.fetch(
            "SELECT * FROM clinical.patient_regimen_order_item "
            "WHERE revision_id=$1 ORDER BY line_no",
            revision["id"],
        )
        checks = []

        def check(code, message, passed):
            checks.append(
                {"code": code, "message": message, "state": "SATISFIED" if passed else "PENDING"}
            )

        check(
            "EXACT_REVISION_CONFIRMED",
            "这次保存的修订已单独确认",
            instance["current_confirmed_revision_id"] == revision["id"],
        )
        check(
            "CURRENT_SNAPSHOT",
            "修订引用的患者快照仍是本次准备快照",
            revision["snapshot_id"] == context["snapshot_id"],
        )
        check(
            "HOSPITAL_ROUTES",
            "指定院方交付配置文件；实际路由另行联通核验",
            bool(workflow.settings.hospital_delivery_config),
        )
        # Test-only confirmation never grants real clinical release, even with routes configured.
        check("CLINICAL_AUTHORIZATION", "真实院方身份、提交权限及临床放行尚未接入", False)
        check(
            "REVIEWED_TEMPLATE",
            "来源方案与关联知识须完成临床审核",
            instance["availability_state"] == "AVAILABLE",
        )
        check(
            "COMPLETE_ORDER_PAYLOAD",
            "实际医嘱需具备完整剂量、数量、治疗日和医院编码",
            revision["compilation_state"] == "COMPLETE"
            and bool(orders)
            and all(order["resolution_state"] == "READY" for order in orders),
        )
        required = (
            "hospital_item_code",
            "dose_value",
            "dose_unit",
            "quantity",
            "quantity_unit",
            "route_code",
            "frequency_code",
            "start_day",
            "long_term_flag",
        )
        lines = [
            {
                "line_no": order["line_no"],
                "name": order["item_name"],
                "missing": [key for key in required if order[key] is None or order[key] == ""],
                "mapping_state": order["resolution_state"],
            }
            for order in orders
        ]
    return {
        "kind": "LOCAL_DELIVERY_READINESS",
        "state": "BLOCKED",
        "revision_id": str(revision["id"]),
        "revision_hash": revision["content_hash"],
        "orders_hash": revision["orders_hash"],
        "checks": checks,
        "lines": lines,
        "hospital_call_started": False,
        "hospital_validation_status": "NOT_REQUESTED",
    }
