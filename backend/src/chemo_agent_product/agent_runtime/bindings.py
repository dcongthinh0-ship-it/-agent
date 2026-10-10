from __future__ import annotations

from uuid import UUID

from chemo_agent_product.agent_runtime import repository
from chemo_agent_product.core.domain import Reference
from chemo_agent_product.core.security import BusinessError


class BindingLoader:
    def __init__(self, runtime):
        self.runtime = runtime

    async def bindings(self, run):
        ref = run["input_reference"]
        refs = [Reference.model_validate(ref["snapshot_ref"])]
        async with self.runtime.pool.acquire() as c, c.transaction(readonly=True):
            await self.runtime.assert_active(c, run)
            snapshot = await repository.get_snapshot_payload(c, UUID(ref["snapshot_ref"]["id"]))
            for fact in snapshot["facts"].values():
                refs.append(Reference.model_validate(fact["source"]))
            for record in snapshot["text_records"]:
                refs.append(Reference.model_validate(record["source"]))
            candidates = await repository.list_bound_candidates(
                c,
                UUID(ref["matching_decision_id"])
                if ref["matching_decision_id"] != "None"
                else run["decision_run_id"],
            )
            for candidate in candidates:
                refs.append(
                    Reference(
                        namespace="catalog_bridge.template_version_reference",
                        id=str(candidate["projection_id"]),
                        version="template-projection.v1",
                        content_hash=candidate["template_hash"],
                    )
                )
            manifest = ref["knowledge_manifest"]
            for group in ["evidence", "applicability", "rule_packages"]:
                refs.extend(Reference.model_validate(r) for r in manifest.get(group, []))
            evidence = {}
            for r in manifest.get("evidence", []):
                row = await repository.get_fixed_evidence(c, UUID(r["id"]))
                if not row or row["content_hash"] != r["content_hash"]:
                    raise BusinessError(
                        "KNOWLEDGE_VERSION_CHANGED", "本次证据固定版本发生变化", 409
                    )
                evidence[r["id"]] = {**dict(row), "ref": r}
            revision = (
                await repository.get_revision(c, UUID(ref["revision_id"]))
                if ref["revision_id"]
                else None
            )
            if revision:
                if revision["content_hash"] != ref["revision_hash"]:
                    raise BusinessError("REVISION_HASH_MISMATCH", "复核修订内容不一致", 409)
                refs.append(
                    Reference(
                        namespace="clinical.patient_regimen_revision",
                        id=str(revision["id"]),
                        version=str(revision["revision_no"]),
                        content_hash=revision["content_hash"],
                    )
                )
                orders = await repository.list_revision_orders(c, revision["id"])
                fields = await repository.list_revision_fields(c, revision["id"])
            else:
                orders = []
                fields = []
        return {
            "snapshot": snapshot,
            "refs": refs,
            "candidates": {r["id"]: dict(r) for r in candidates},
            "evidence": evidence,
            "manifest": manifest,
            "revision": dict(revision) if revision else None,
            "orders": [dict(r) for r in orders],
            "field_values": {r["field_key"]: r["value_json"] for r in fields},
            "field_provenance": {
                r["field_key"]: {k: r[k] for k in ("value_state", "value_source", "provenance_ref")}
                for r in fields
            },
        }
