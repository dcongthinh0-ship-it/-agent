from __future__ import annotations

from pathlib import Path

from chemo_agent_product.agent_runtime.contracts import AgentOutput, Statement
from chemo_agent_product.core.domain import Reference
from chemo_agent_product.core.security import BusinessError

TOOLS = (
    "read_snapshot",
    "read_candidates",
    "read_plan",
    "read_evidence",
    "read_rules",
    "read_calculations",
    "read_revision",
    "search_knowledge",
)


def prompt_path(kind: str):
    return (
        Path(__file__).resolve().parent
        / "prompts"
        / ("reviewer.v1.md" if kind == "REVIEWER" else "recommendation.v1.md")
    )


def validate_output(payload, kind, allowed_refs, text_records, candidate_ids):
    output = AgentOutput.model_validate(payload)
    if output.kind != kind:
        raise BusinessError("AGENT_KIND_MISMATCH", "模型输出类型不符合任务", 502)
    allowed = {ref.model_dump_json() for ref in allowed_refs}
    if output.summary and (not output.summary_source_refs):
        raise BusinessError("AGENT_SUMMARY_UNSOURCED", "模型摘要缺少本次来源", 502)
    if any(ref.model_dump_json() not in allowed for ref in output.summary_source_refs):
        raise BusinessError("AGENT_REFERENCE_INVALID", "模型摘要引用不属于本次资料", 502)
    for item in [*output.statements, *output.findings, *output.proposed_facts]:
        if any(ref.model_dump_json() not in allowed for ref in item.source_refs):
            raise BusinessError("AGENT_REFERENCE_INVALID", "模型引用不属于本次固定资料", 502)
        if (
            isinstance(item, Statement)
            and item.candidate_id
            and (item.candidate_id not in candidate_ids)
        ):
            raise BusinessError("AGENT_CANDIDATE_INVALID", "模型输出引用了其他候选", 502)
    records = {r["record_id"]: r for r in text_records}
    for fact in output.proposed_facts:
        record = records.get(fact.record_id)
        if (
            not record
            or fact.end <= fact.start
            or fact.end > len(record["text"])
            or (record["text"][fact.start : fact.end] != fact.quote)
        ):
            raise BusinessError("AGENT_QUOTE_INVALID", "候选事实无法对应原文位置", 502)
        if Reference.model_validate(record["source"]).model_dump_json() not in {
            r.model_dump_json() for r in fact.source_refs
        }:
            raise BusinessError("AGENT_FACT_SOURCE_INVALID", "候选事实缺少原文记录引用", 502)
    if kind == "REVIEWER" and output.proposed_facts:
        raise BusinessError("REVIEWER_FACT_WRITE_FORBIDDEN", "复核任务不能提出替换患者事实", 502)
    return output
