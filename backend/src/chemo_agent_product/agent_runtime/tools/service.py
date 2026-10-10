from __future__ import annotations

from datetime import UTC, datetime

from chemo_agent_product.agent_runtime import repository
from chemo_agent_product.core.domain import fingerprint
from chemo_agent_product.core.security import BusinessError

from .dispatch import read_bound_tool


class ClinicalToolService:
    def __init__(self, runtime):
        self.runtime = runtime

    async def call(self, run, b, name, args, job, worker):
        async with self.runtime.pool.acquire() as c, c.transaction():
            await worker.fenced(c, job)
            await self.runtime.assert_active(c, run)
            await repository.lock_run(c, run["id"])
            number = await repository.next_call_number(c, run["id"])
            safe = {k: v for k, v in args.items() if k in {"candidate_id", "evidence_id"}}
            if "query" in args:
                safe.update(
                    query_hash=fingerprint(args["query"]), query_length=len(str(args["query"]))
                )
            call = await repository.insert_agent_tool_call(
                c,
                dict(
                    created_by_principal=run["created_by_principal"],
                    agent_run_id=run["id"],
                    call_no=number,
                    tool_name=name,
                    tool_category="CLINICAL_READ",
                    request_schema_version="clinical-tools.v1",
                    request_safe_args=safe,
                    request_hash=fingerprint(args),
                    status="STARTED",
                    started_at=datetime.now(UTC),
                ),
            )
        try:
            result = read_bound_tool(run, b, name, args, self.runtime.workflow.initial_fields)
            async with self.runtime.pool.acquire() as c, c.transaction():
                await worker.fenced(c, job)
                await self.runtime.assert_active(c, run)
                await repository.complete_tool_call(
                    c,
                    call["id"],
                    {"contract": "clinical-tools.v1", "tool": name},
                    fingerprint(result),
                )
            return result
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, BusinessError)
                else "TOOL_ARGUMENT_INVALID"
                if isinstance(exc, (ValueError, TypeError))
                else "TOOL_READ_FAILED"
            )
            async with self.runtime.pool.acquire() as c:
                await repository.fail_tool_call(
                    c, call["id"], code, "FAILED" if code == "TOOL_READ_FAILED" else "DENIED"
                )
            raise BusinessError(code, "工具请求不符合本次患者范围或输入合同", 403) from None
