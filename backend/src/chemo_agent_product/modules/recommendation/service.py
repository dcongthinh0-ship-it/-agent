from __future__ import annotations

from uuid import UUID

from chemo_agent_product.core.domain import PatientSnapshot
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.modules.recommendation import repository as repository


class CandidateService:
    def __init__(self, workflow):
        self.workflow = workflow

    async def candidate(self, c, context_id, p, candidate_id):
        context = await self.workflow.scoped_context(c, context_id, p)
        row = await repository.get_current_candidate(
            c, candidate_id, context_id, context["current_prepare_run_id"]
        )
        if not row:
            raise BusinessError("CANDIDATE_STALE", "候选不属于当前准备结果，请刷新", 409)
        return context, row

    async def detail(self, p, context_id, candidate_id):
        async with self.workflow.pool.acquire() as c, c.transaction(readonly=True):
            context, row = await self.workflow.candidate(c, context_id, p, candidate_id)
            snapshot = await repository.get_snapshot_payload(c, row["snapshot_id"])
        return {
            "candidate_id": str(row["id"]),
            "template": row["template_payload"],
            "template_hash": row["template_hash"],
            "snapshot": snapshot,
            "field_values": self.workflow.initial_fields(
                row["template_payload"], PatientSnapshot.model_validate(snapshot)
            ),
            "assessment": dict(row),
        }

    async def candidate_evidence(self, p, context_id, candidate_id):
        """Read exact versions in the saved assessment, never today's replacement evidence."""
        async with self.workflow.pool.acquire() as c, c.transaction(readonly=True):
            _, candidate = await self.workflow.candidate(c, context_id, p, candidate_id)
            records = []
            for ref in candidate["evidence_refs"] or []:
                if ref["namespace"] != "knowledge.evidence_record_version":
                    raise BusinessError(
                        "EVIDENCE_NAMESPACE_INVALID", "本次证据来源命名空间不正确", 409
                    )
                row = await repository.get_fixed_evidence(c, UUID(ref["id"]))
                if (
                    not row
                    or row["content_hash"] != ref["content_hash"]
                    or str(row["version_no"]) != ref["version"]
                ):
                    raise BusinessError(
                        "KNOWLEDGE_VERSION_CHANGED", "本次证据固定版本不一致，请核对来源", 409
                    )
                records.append(
                    {
                        "ref": ref,
                        "source": "FDA"
                        if row["source_code"] in {"FDA", "DAILYMED"}
                        else row["source_code"],
                        "excerpt": row["verbatim_excerpt"],
                        "locator": row["source_locator"],
                        "native_recommendation": row["source_recommendation_raw"],
                        "native_category": row["source_evidence_category_raw"],
                    }
                )
        return {"candidate_id": str(candidate_id), "items": records}

    async def action(self, p, context_id, request, idempotency_key):
        async with self.workflow.pool.acquire() as c, c.transaction():
            context = await self.workflow.scoped_context(c, context_id, p)
            cmd, cached = await self.workflow.command(
                c,
                p,
                request.kind,
                idempotency_key,
                {"context_id": str(context_id), **request.model_dump(mode="json")},
            )
            if cached:
                return cached
            for candidate_id in request.candidate_ids:
                await self.workflow.candidate(c, context_id, p, candidate_id)
            result = {
                "action": request.kind,
                "candidate_ids": [str(i) for i in request.candidate_ids],
            }
            await self.workflow.audit(
                c, p, context, f"CANDIDATE_{request.kind}", context_id, result
            )
            await self.workflow.complete(c, cmd, result, "launch_context", context_id)
            return result
