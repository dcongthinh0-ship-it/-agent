from uuid import uuid4

import pytest
from pydantic import SecretStr

from chemo_agent_product.agent_adapter import TOOLS, ClaudeAdapter, validate_output
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
    assert options.allowed_tools == [f"mcp__clinical__{name}" for name in TOOLS]
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""


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
