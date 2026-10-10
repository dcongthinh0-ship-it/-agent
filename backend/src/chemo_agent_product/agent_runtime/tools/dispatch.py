from __future__ import annotations

from uuid import UUID

from chemo_agent_product.agent_runtime.policy import TOOLS
from chemo_agent_product.core.domain import Reference
from chemo_agent_product.core.security import BusinessError


def read_bound_tool(run, b, name, args, initial_fields):
    if name not in TOOLS:
        raise BusinessError("TOOL_DENIED", "该工具未授权", 403)
    expected = (
        {"candidate_id"}
        if name == "read_plan"
        else {"evidence_id"}
        if name == "read_evidence"
        else {"query"}
        if name == "search_knowledge"
        else set()
    )
    if set(args) != expected:
        raise BusinessError("TOOL_ARGUMENT_INVALID", "工具输入不符合合同", 422)
    if name == "read_snapshot":
        result = {"snapshot": b["snapshot"], "ref": run["input_reference"]["snapshot_ref"]}
    elif name == "read_candidates":
        result = {
            "items": [
                {k: v for k, v in r.items() if k != "template_payload"}
                for r in b["candidates"].values()
            ]
        }
    elif name == "read_plan":
        row = b["candidates"].get(UUID(args["candidate_id"]))
        if not row:
            raise BusinessError("TOOL_SCOPE_DENIED", "方案不属于本次候选", 403)
        result = row
    elif name == "read_evidence":
        result = b["evidence"].get(str(UUID(args["evidence_id"])))
        if not result:
            raise BusinessError("TOOL_SCOPE_DENIED", "证据不属于本次资料", 403)
    elif name == "read_rules":
        result = {
            "manifest": b["manifest"],
            "candidates": [
                {k: r[k] for k in ["id", "data_labels", "safety_labels", "x_basis"]}
                for r in b["candidates"].values()
            ],
        }
    elif name == "read_revision":
        result = {
            "revision": b["revision"],
            "ref": Reference(
                namespace="clinical.patient_regimen_revision",
                id=str(b["revision"]["id"]),
                version=str(b["revision"]["revision_no"]),
                content_hash=b["revision"]["content_hash"],
            ).model_dump(mode="json")
            if b["revision"]
            else None,
            "orders": b["orders"],
            "field_values": b["field_values"],
            "field_provenance": b["field_provenance"],
        }
    elif name == "read_calculations":
        if b["revision"]:
            saved = b["revision"]["selection_manifest"]
            result = {
                "state": "SAVED_REVISION",
                "bsa": saved.get("bsa"),
                "reference_doses": saved.get("reference_doses", {}),
                "revision_id": str(b["revision"]["id"]),
            }
        else:
            from chemo_agent_product.core.domain import PatientSnapshot
            from chemo_agent_product.modules.patient_regimen.compiler import compile_revision
            from chemo_agent_product.modules.patient_regimen.schemas import SaveInput

            snapshot = PatientSnapshot.model_validate(b["snapshot"])
            items = []
            for candidate in b["candidates"].values():
                template = candidate["template_payload"]
                calculated = compile_revision(
                    template,
                    snapshot,
                    SaveInput(expected_row_version=1),
                    initial_fields(template, snapshot),
                    b["manifest"].get("calculation", {}),
                )
                items.append(
                    {
                        "candidate_id": str(candidate["id"]),
                        "bsa": calculated["manifest"]["bsa"],
                        "reference_doses": calculated["manifest"]["reference_doses"],
                    }
                )
            result = {"state": "CONTROLLED_REFERENCE_ONLY", "items": items}
    else:
        query = str(args["query"]).strip()
        if not query or len(query) > 200:
            raise BusinessError("TOOL_ARGUMENT_INVALID", "检索文字长度无效", 422)
        result = {
            "items": [
                e
                for e in b["evidence"].values()
                if query.casefold() in e["verbatim_excerpt"].casefold()
            ][:20]
        }
    return result
