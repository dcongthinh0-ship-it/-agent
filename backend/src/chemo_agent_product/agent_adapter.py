"""Claude Agent SDK boundary without environment access, ambient skills or fake responses."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from chemo_agent_product.config import Settings
from chemo_agent_product.domain import Contract, Reference
from chemo_agent_product.security import BusinessError


class Statement(Contract):
    message: str = Field(min_length=1, max_length=3000)
    candidate_id: UUID | None = None
    source_refs: list[Reference] = Field(min_length=1, max_length=30)


class ReviewFinding(Statement):
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["INFO", "ATTENTION", "HIGH"]
    field_path: str | None = None


class FactProposal(Contract):
    fact_code: str = Field(min_length=1, max_length=100)
    value: str | float | bool | None
    status: Literal["ASSERTED", "NEGATED", "UNCERTAIN", "CONFLICT"]
    record_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2000)
    source_refs: list[Reference] = Field(min_length=1)


class AgentOutput(Contract):
    schema_version: Literal["agent-output.v1"] = "agent-output.v1"
    kind: Literal["RECOMMENDATION", "REVIEWER"]
    summary: str = Field(max_length=4000)
    summary_source_refs: list[Reference] = Field(min_length=1, max_length=50)
    statements: list[Statement] = Field(default_factory=list, max_length=100)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    proposed_facts: list[FactProposal] = Field(default_factory=list, max_length=100)
    pending_questions: list[str] = Field(default_factory=list, max_length=100)


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


def sdk_version():
    return importlib.metadata.version("claude-agent-sdk")


def validate_output(payload, kind, allowed_refs, text_records, candidate_ids):
    output = AgentOutput.model_validate(payload)
    if output.kind != kind:
        raise BusinessError("AGENT_KIND_MISMATCH", "模型输出类型不符合任务", 502)
    allowed = {ref.model_dump_json() for ref in allowed_refs}
    if output.summary and not output.summary_source_refs:
        raise BusinessError("AGENT_SUMMARY_UNSOURCED", "模型摘要缺少本次来源", 502)
    if any(ref.model_dump_json() not in allowed for ref in output.summary_source_refs):
        raise BusinessError("AGENT_REFERENCE_INVALID", "模型摘要引用不属于本次资料", 502)
    for item in [*output.statements, *output.findings, *output.proposed_facts]:
        if any(ref.model_dump_json() not in allowed for ref in item.source_refs):
            raise BusinessError("AGENT_REFERENCE_INVALID", "模型引用不属于本次固定资料", 502)
        if (
            isinstance(item, Statement)
            and item.candidate_id
            and item.candidate_id not in candidate_ids
        ):
            raise BusinessError("AGENT_CANDIDATE_INVALID", "模型输出引用了其他候选", 502)
    records = {r["record_id"]: r for r in text_records}
    for fact in output.proposed_facts:
        record = records.get(fact.record_id)
        if (
            not record
            or fact.end <= fact.start
            or fact.end > len(record["text"])
            or record["text"][fact.start : fact.end] != fact.quote
        ):
            raise BusinessError("AGENT_QUOTE_INVALID", "候选事实无法对应原文位置", 502)
        if Reference.model_validate(record["source"]).model_dump_json() not in {
            r.model_dump_json() for r in fact.source_refs
        }:
            raise BusinessError("AGENT_FACT_SOURCE_INVALID", "候选事实缺少原文记录引用", 502)
    if kind == "REVIEWER" and output.proposed_facts:
        raise BusinessError("REVIEWER_FACT_WRITE_FORBIDDEN", "复核任务不能提出替换患者事实", 502)
    return output


class ClaudeAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def options(self, kind, prompt, server, cwd, source_refs=None):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, PermissionResultDeny

        # SDK final JSON delivery is separate from access to the environment.
        allowed_tools = [f"mcp__clinical__{name}" for name in TOOLS] + ["StructuredOutput"]

        async def deny(name, args, context):
            return PermissionResultDeny(
                message="Only the bound clinical read tools are permitted", interrupt=True
            )

        async def restrict(input_data, tool_use_id, context):
            name = input_data.get("tool_name")
            if name not in allowed_tools:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "Only bound clinical tools and structured output are permitted"
                        ),
                    }
                }
            return {}

        config = self.settings
        if not config.model_configured:
            raise BusinessError("MODEL_NOT_CONFIGURED", "模型密钥和模型名称尚未配置", 503)
        env = {
            "ANTHROPIC_API_KEY": config.model_api_key.get_secret_value(),
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CONFIG_DIR": str(Path(cwd) / ".claude-runtime"),
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "CLAUDE_CODE_USE_VERTEX": "0",
            "CLAUDE_CODE_USE_FOUNDRY": "0",
        }
        if config.model_base_url:
            env["ANTHROPIC_BASE_URL"] = config.model_base_url
        schema = AgentOutput.model_json_schema()
        if source_refs is not None:
            # The SDK validates exact frozen tuples before delivering final JSON.
            schema["$defs"]["Reference"]["enum"] = source_refs
        return ClaudeAgentOptions(
            tools=[],
            allowed_tools=allowed_tools,
            disallowed_tools=[
                "Bash",
                "Read",
                "Write",
                "Edit",
                "Glob",
                "Grep",
                "WebFetch",
                "WebSearch",
                "Agent",
                "Task",
            ],
            system_prompt=prompt,
            mcp_servers={"clinical": server},
            strict_mcp_config=True,
            setting_sources=[],
            skills=[],
            plugins=[],
            permission_mode="dontAsk",
            can_use_tool=deny,
            hooks={"PreToolUse": [HookMatcher(hooks=[restrict])]},
            model=config.reviewer_model_name
            if kind == "REVIEWER" and config.reviewer_model_name
            else config.model_name,
            env=env,
            cwd=cwd,
            max_turns=config.model_max_turns,
            max_budget_usd=config.model_max_budget_usd,
            output_format={"type": "json_schema", "schema": schema},
            include_partial_messages=False,
            stderr=lambda line: None,
        )

    async def run(self, kind: str, input_payload: dict, dispatch):
        from claude_agent_sdk import (
            ClaudeSDKClient,
            ResultMessage,
            ToolAnnotations,
            create_sdk_mcp_server,
            tool,
        )

        definitions = []
        descriptions = {
            "read_snapshot": "Read the exact patient snapshot bound to this run",
            "read_candidates": "Read deterministic candidate order, regions, levels and labels",
            "read_plan": "Read one fixed candidate form by candidate_id",
            "read_evidence": "Read one evidence version from the frozen manifest by evidence_id",
            "read_rules": "Read the frozen rule results and knowledge manifest",
            "read_calculations": "Read controlled formula results and missing-input reasons",
            "read_revision": "Read the exact saved revision being reviewed",
            "search_knowledge": "Search literal text in evidence associated with this run",
        }
        for name in TOOLS:
            properties = {}
            if name == "read_plan":
                properties = {"candidate_id": {"type": "string", "format": "uuid"}}
            if name == "read_evidence":
                properties = {"evidence_id": {"type": "string", "format": "uuid"}}
            if name == "search_knowledge":
                properties = {"query": {"type": "string", "minLength": 1, "maxLength": 200}}
            schema = {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }

            def bind(tool_name):
                async def handler(args):
                    result = await dispatch(tool_name, args)
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, default=str),
                            }
                        ]
                    }

                return handler

            definitions.append(
                tool(
                    name,
                    descriptions[name],
                    schema,
                    # Keep bound results inline; the Agent cannot read SDK spill files.
                    annotations=ToolAnnotations(readOnlyHint=True, maxResultSizeChars=300_000),
                )(bind(name))
            )
        server = create_sdk_mcp_server(name="clinical", version="1.0.0", tools=definitions)
        with tempfile.TemporaryDirectory(prefix="chemo-agent-isolated-") as cwd:
            options = self.options(
                kind,
                prompt_path(kind).read_text(),
                server,
                cwd,
                input_payload.get("source_reference_catalog"),
            )
            async with asyncio.timeout(self.settings.model_timeout_seconds):
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(json.dumps(input_payload, ensure_ascii=False, default=str))
                    async for message in client.receive_response():
                        # Only return the structured result; private model traces stay internal.
                        if isinstance(message, ResultMessage):
                            if message.is_error or message.structured_output is None:
                                raise BusinessError(
                                    "MODEL_OUTPUT_UNAVAILABLE", "模型未返回有效结构化结果", 502
                                )
                            return message.structured_output, {
                                "cost_usd": message.total_cost_usd,
                                "turns": message.num_turns,
                                "duration_ms": message.duration_ms,
                                "usage": message.usage or {},
                            }
        raise BusinessError("MODEL_OUTPUT_UNAVAILABLE", "模型运行未完成", 502)
