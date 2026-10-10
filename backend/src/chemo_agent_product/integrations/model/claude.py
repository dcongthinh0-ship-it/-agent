from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import re
import tempfile
from pathlib import Path
from time import perf_counter

from chemo_agent_product.agent_runtime.contracts import AgentOutput
from chemo_agent_product.agent_runtime.policy import TOOLS, prompt_path
from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.observability import emit
from chemo_agent_product.core.security import BusinessError

"Claude Agent SDK boundary. No environment tools, ambient skills, credentials or fake responses."

logger = logging.getLogger(__name__)


class StderrDiagnostics:
    """Discard all free text; emit at most three classified status events per run."""

    def __init__(self):
        self.lines = 0

    def __call__(self, line):
        self.lines += 1
        if self.lines > 3:
            return
        match = re.search(
            r"(?:API Error:|HTTP status:)\s*(401|403|429|5\d{2})\b", line[:512], flags=re.IGNORECASE
        )
        status = int(match[1]) if match else None
        diagnostic = (
            "AUTH"
            if status in {401, 403}
            else "RATE_LIMIT"
            if status == 429
            else "UPSTREAM"
            if status
            else "UNCLASSIFIED"
        )
        emit(
            logger,
            "model_stderr",
            level=logging.WARNING if status else logging.INFO,
            http_status=status,
            diagnostic=diagnostic,
        )

    def finish(self):
        if self.lines:
            emit(
                logger,
                "model_stderr_summary",
                stderr_lines=self.lines,
                suppressed_lines=max(0, self.lines - 3),
            )


def sdk_version():
    return importlib.metadata.version("claude-agent-sdk")


class ClaudeAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def options(self, kind, prompt, server, cwd, stderr=None):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, PermissionResultDeny

        # The SDK uses this internal tool to deliver output_format results.
        # It grants no environment access; application output validation still applies.
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
            output_format={"type": "json_schema", "schema": AgentOutput.model_json_schema()},
            include_partial_messages=False,
            stderr=stderr or StderrDiagnostics(),
        )

    async def run(self, kind: str, input_payload: dict, dispatch):
        from claude_agent_sdk import create_sdk_mcp_server, tool
        from mcp.types import ToolAnnotations

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
                    name, descriptions[name], schema, annotations=ToolAnnotations(readOnlyHint=True)
                )(bind(name))
            )
        server = create_sdk_mcp_server(name="clinical", version="1.0.0", tools=definitions)
        started = perf_counter()
        diagnostics = StderrDiagnostics()
        emit(logger, "model_started", profile_kind=kind)
        try:
            return await self.receive(kind, input_payload, server, diagnostics)
        except asyncio.CancelledError:
            emit(
                logger,
                "model_cancelled",
                profile_kind=kind,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            raise
        except Exception as exc:
            emit(
                logger,
                "model_failed",
                level=logging.ERROR,
                exc=exc,
                profile_kind=kind,
                error_code=exc.code if isinstance(exc, BusinessError) else "MODEL_RUN_FAILED",
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            raise
        finally:
            diagnostics.finish()

    async def receive(self, kind, input_payload, server, diagnostics):
        from claude_agent_sdk import ClaudeSDKClient, ResultMessage

        with tempfile.TemporaryDirectory(prefix="chemo-agent-isolated-") as cwd:
            options = self.options(kind, prompt_path(kind).read_text(), server, cwd, diagnostics)
            async with asyncio.timeout(self.settings.model_timeout_seconds):
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(json.dumps(input_payload, ensure_ascii=False, default=str))
                    async for message in client.receive_response():
                        if isinstance(message, ResultMessage):
                            usage = message.usage or {}
                            emit(
                                logger,
                                "model_completed",
                                profile_kind=kind,
                                status="FAILED"
                                if message.is_error or message.structured_output is None
                                else "SUCCEEDED",
                                cost_usd=message.total_cost_usd,
                                turns=message.num_turns,
                                duration_ms=message.duration_ms,
                                **{
                                    key: usage.get(key)
                                    for key in (
                                        "input_tokens",
                                        "output_tokens",
                                        "cache_read_input_tokens",
                                        "cache_creation_input_tokens",
                                    )
                                },
                            )
                            if message.is_error or message.structured_output is None:
                                raise BusinessError(
                                    "MODEL_OUTPUT_UNAVAILABLE", "模型未返回有效结构化结果", 502
                                )
                            return (
                                message.structured_output,
                                {
                                    "cost_usd": message.total_cost_usd,
                                    "turns": message.num_turns,
                                    "duration_ms": message.duration_ms,
                                    "usage": message.usage or {},
                                },
                            )
        raise BusinessError("MODEL_OUTPUT_UNAVAILABLE", "模型运行未完成", 502)
