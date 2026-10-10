from uuid import uuid4

import pytest
from pydantic import SecretStr

from chemo_agent_product.agent_runtime.contracts import AgentOutput
from chemo_agent_product.agent_runtime.policy import TOOLS, validate_output
from chemo_agent_product.agent_runtime.tools.dispatch import read_bound_tool
from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.domain import Reference
from chemo_agent_product.core.security import BusinessError
from chemo_agent_product.integrations.model.claude import ClaudeAdapter

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
async def test_structured_output_can_pass_pretool_hook_for_both_agent_profiles(kind):
    config = Settings(
        _env_file=None,
        model_enabled=True,
        model_api_key=SecretStr("CONTRACT_ONLY"),
        model_name="CONTRACT_ONLY",
    )
    options = ClaudeAdapter(config).options(kind, "contract", {}, "/tmp")
    assert options.output_format == {
        "type": "json_schema",
        "schema": AgentOutput.model_json_schema(),
    }
    restrict = options.hooks["PreToolUse"][0].hooks[0]
    for name in ["StructuredOutput", *[f"mcp__clinical__{tool}" for tool in TOOLS]]:
        decision = await restrict({"tool_name": name, "tool_input": {}}, "contract", {})
        assert decision == {}, name
        assert name in options.allowed_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["RECOMMENDATION", "REVIEWER"])
async def test_output_permission_does_not_allow_environment_or_unknown_tools(kind):
    config = Settings(
        _env_file=None,
        model_enabled=True,
        model_api_key=SecretStr("CONTRACT_ONLY"),
        model_name="CONTRACT_ONLY",
    )
    options = ClaudeAdapter(config).options(kind, "contract", {}, "/tmp")
    restrict = options.hooks["PreToolUse"][0].hooks[0]
    forbidden = [
        *options.disallowed_tools,
        "StructuredOutputExtra",
        "structuredoutput",
        "mcp__other__StructuredOutput",
        "mcp__clinical__StructuredOutput",
        "mcp__clinical__write_revision",
        "mcp__clinical__submit_orders",
        None,
    ]
    for name in forbidden:
        assert name not in options.allowed_tools
        decision = await restrict({"tool_name": name, "tool_input": {}}, "contract", {})
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny", name
    fallback = await options.can_use_tool("unknown_tool", {}, {})
    assert fallback.behavior == "deny" and fallback.interrupt


def test_revision_tool_provides_exact_reference_without_accepting_guessed_namespace():
    revision = {"id": uuid4(), "revision_no": 2, "content_hash": "b" * 64}
    bindings = {"revision": revision, "orders": [], "field_values": {}, "field_provenance": {}}
    result = read_bound_tool({}, bindings, "read_revision", {}, None)
    ref = Reference(
        namespace="clinical.patient_regimen_revision",
        id=str(revision["id"]),
        version="2",
        content_hash=revision["content_hash"],
    )
    assert result["ref"] == ref.model_dump(mode="json")
    item = {**payload(), "kind": "REVIEWER", "summary_source_refs": [result["ref"]]}
    assert validate_output(item, "REVIEWER", [ref], [], set()).kind == "REVIEWER"
    item["summary_source_refs"][0]["namespace"] = "clinical.plan_revision"
    with pytest.raises(BusinessError, match="AGENT_REFERENCE_INVALID"):
        validate_output(item, "REVIEWER", [ref], [], set())
    bindings["revision"] = None
    assert read_bound_tool({}, bindings, "read_revision", {}, None)["ref"] is None


def test_missing_model_never_returns_fixed_agent_text():
    with pytest.raises(BusinessError, match="MODEL_NOT_CONFIGURED"):
        ClaudeAdapter(Settings()).options("RECOMMENDATION", "contract", {}, "/tmp")


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
