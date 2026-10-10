import json
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from chemo_agent_product.agent_adapter import TOOLS, AgentOutput, ClaudeAdapter, validate_output
from chemo_agent_product.config import Settings
from chemo_agent_product.domain import Reference
from chemo_agent_product.security import BusinessError

REF = Reference(namespace="contract", id="source", version="1", content_hash="a" * 64)


def payload():
    return {
        "kind": "RECOMMENDATION",
        "summary": "合同测试",
        "summary_source_refs": [REF.model_dump(mode="json")],
        "statements": [],
        "findings": [],
        "proposed_facts": [],
        "pending_questions": [],
    }


def test_model_options_never_inherit_builtins_skills_or_bypass_permissions():
    config = Settings(
        model_enabled=True, model_api_key=SecretStr("CONTRACT_ONLY"), model_name="CONTRACT_ONLY"
    )
    options = ClaudeAdapter(config).options("RECOMMENDATION", "contract", {}, "/tmp")
    assert options.tools == [] and options.skills == [] and options.setting_sources == []
    assert options.permission_mode == "dontAsk"
    assert options.allowed_tools == [f"mcp__clinical__{name}" for name in TOOLS] + [
        "StructuredOutput"
    ]
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["RECOMMENDATION", "REVIEWER"])
async def test_structured_output_allowed_without_environment_or_write_access(kind):
    config = Settings(
        _env_file=None,
        model_enabled=True,
        model_api_key=SecretStr("CONTRACT_ONLY"),
        model_name="CONTRACT_ONLY",
    )
    options = ClaudeAdapter(config).options(kind, "contract", {}, "/tmp")
    restrict = options.hooks["PreToolUse"][0].hooks[0]
    for name in ["StructuredOutput", *[f"mcp__clinical__{tool}" for tool in TOOLS]]:
        assert name in options.allowed_tools
        assert await restrict({"tool_name": name}, "contract", {}) == {}
    for name in [
        *options.disallowed_tools,
        "StructuredOutputExtra",
        "mcp__clinical__StructuredOutput",
        "mcp__other__read_snapshot",
        "mcp__clinical__submit_orders",
    ]:
        assert name not in options.allowed_tools
        result = await restrict({"tool_name": name}, "contract", {})
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_large_bound_results_advertise_inline_limit_on_mcp_wire(monkeypatch):
    import claude_agent_sdk as sdk

    wire_tools = []
    definitions = {}
    build_server = sdk.build_tool_server
    create_server = sdk.create_sdk_mcp_server
    large_result = {"candidates": "合同资料" * 20_000}

    def capture_wire(name, version, tools, run_tool):
        wire_tools.extend(item.model_dump(by_alias=True) for item in tools)
        return build_server(name, version, tools, run_tool)

    def capture_definitions(**kwargs):
        definitions.update({item.name: item for item in kwargs["tools"]})
        return create_server(**kwargs)

    class ContractClient:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def query(self, _query):
            reply = await definitions["read_candidates"].handler({})
            text = reply["content"][0]["text"]
            assert 50_000 < len(text) < 200_000
            assert json.loads(text) == large_result

        async def receive_response(self):
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="contract",
                structured_output=payload(),
            )

    async def dispatch(name, args):
        assert name == "read_candidates" and args == {}
        return large_result

    monkeypatch.setattr(sdk, "build_tool_server", capture_wire)
    monkeypatch.setattr(sdk, "create_sdk_mcp_server", capture_definitions)
    monkeypatch.setattr(sdk, "ClaudeSDKClient", ContractClient)
    config = Settings(
        _env_file=None,
        model_enabled=True,
        model_api_key=SecretStr("CONTRACT_ONLY"),
        model_name="CONTRACT_ONLY",
    )
    await ClaudeAdapter(config).run("RECOMMENDATION", {}, dispatch)
    assert {item["name"] for item in wire_tools} == set(TOOLS)
    for item in wire_tools:
        assert item["annotations"]["readOnlyHint"] is True
        assert item["_meta"]["anthropic/maxResultSizeChars"] == 300_000


def test_missing_model_never_returns_fixed_agent_text():
    with pytest.raises(BusinessError, match="MODEL_NOT_CONFIGURED"):
        ClaudeAdapter(Settings()).options("RECOMMENDATION", "contract", {}, "/tmp")


def test_summary_sources_are_required_by_sdk_schema_and_output_contract():
    schema = AgentOutput.model_json_schema()
    assert "summary_source_refs" in schema["required"]
    assert schema["properties"]["summary_source_refs"]["minItems"] == 1
    item = payload()
    del item["summary_source_refs"]
    with pytest.raises(ValidationError):
        validate_output(item, "RECOMMENDATION", [REF], [], set())
    item["summary_source_refs"] = []
    with pytest.raises(ValidationError):
        validate_output(item, "RECOMMENDATION", [REF], [], set())
    item["summary_source_refs"] = [REF.model_dump(mode="json")]
    assert validate_output(item, "RECOMMENDATION", [REF], [], set()).summary_source_refs == [REF]


def test_unknown_reference_and_out_of_range_quote_rejected():
    item = payload()
    item["summary_source_refs"][0]["id"] = "unknown"
    with pytest.raises(BusinessError, match="AGENT_REFERENCE_INVALID"):
        validate_output(item, "RECOMMENDATION", [REF], [], set())
    item = payload()
    item["proposed_facts"] = [
        {
            "fact_code": "pathology",
            "value": "contract",
            "status": "UNCERTAIN",
            "record_id": "text1",
            "start": 0,
            "end": 50,
            "quote": "contract",
            "source_refs": [REF.model_dump(mode="json")],
        }
    ]
    with pytest.raises(BusinessError, match="AGENT_QUOTE_INVALID"):
        validate_output(
            item,
            "RECOMMENDATION",
            [REF],
            [{"record_id": "text1", "text": "contract", "source": REF.model_dump(mode="json")}],
            set(),
        )


def test_exact_quote_preserves_uncertainty_without_overwriting_facts():
    item = payload()
    item["proposed_facts"] = [
        {
            "fact_code": "pathology",
            "value": "contract",
            "status": "NEGATED",
            "record_id": "text1",
            "start": 0,
            "end": 8,
            "quote": "contract",
            "source_refs": [REF.model_dump(mode="json")],
        }
    ]
    result = validate_output(
        item,
        "RECOMMENDATION",
        [REF],
        [{"record_id": "text1", "text": "contract", "source": REF.model_dump(mode="json")}],
        set(),
    )
    assert result.proposed_facts[0].status == "NEGATED"


def test_candidate_must_belong_to_bound_run():
    item = payload()
    item["statements"] = [
        {
            "candidate_id": str(uuid4()),
            "message": "contract",
            "source_refs": [REF.model_dump(mode="json")],
        }
    ]
    with pytest.raises(BusinessError, match="AGENT_CANDIDATE_INVALID"):
        validate_output(item, "RECOMMENDATION", [REF], [], set())


def test_agent_plan_retains_complete_clinical_content_and_fixed_source_without_mutation():
    from chemo_agent_product.agents import plan_for_agent

    projection_id = uuid4()
    template = {
        "fields": [{"field_key": "dose", "value": 100}],
        "medications": [{"drug": "合同药物"}],
        "document_tree": [{"text": "原文注意事项"}],
        "content_blocks": [{"text": "原文方案内容"}],
        "word_layout": {"font": "宋体", "runs": "重复排版" * 50_000},
        "layout_verification": {"text_matches_source": True},
    }
    row = {
        "id": uuid4(),
        "projection_id": projection_id,
        "template_hash": "b" * 64,
        "template_payload": template,
        "evidence_refs": [REF.model_dump(mode="json")],
    }
    result = plan_for_agent(row)
    assert result["template_payload"] == {
        k: v for k, v in template.items() if k != "word_layout"
    }
    assert result["ref"] == {
        "namespace": "catalog_bridge.template_version_reference",
        "id": str(projection_id),
        "version": "template-projection.v1",
        "content_hash": "b" * 64,
    }
    assert result["evidence_refs"] == row["evidence_refs"]
    assert "word_layout" in row["template_payload"]
