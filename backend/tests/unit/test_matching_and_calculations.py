"""Ephemeral contract values only; no demo dataset or database patients are created."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from chemo_agent_product.core.domain import (
    CandidateInput,
    Fact,
    FactRequirement,
    PatientSnapshot,
    Reference,
)
from chemo_agent_product.modules.patient_regimen.calculations import bsa, standard_dose
from chemo_agent_product.modules.recommendation.matching import assess

NOW = datetime(2026, 10, 7, tzinfo=UTC)
REF = Reference(namespace="contract-test", id="source", version="1", content_hash="a" * 64)


def snapshot() -> PatientSnapshot:
    return PatientSnapshot(
        patient_ref="CONTRACT_ONLY",
        encounter_ref="CONTRACT_ONLY",
        captured_at=NOW,
        facts={
            key: Fact(
                code=key, value=value, unit=unit, observed_at=NOW, status="CONFIRMED", source=REF
            )
            for key, value, unit in [
                ("disease", "D", None),
                ("pathology", "P", None),
                ("height", 170.0, "cm"),
                ("weight", 60.0, "kg"),
            ]
        },
    )


def plan(code: str = "B", source: str = "CSCO") -> CandidateInput:
    return CandidateInput(
        regimen_id=uuid4(),
        version_id=uuid4(),
        regimen_code=code,
        display_name="合同方案",
        template_hash="b" * 64,
        template_status="PUBLISHED",
        applicability={
            "ref": REF,
            "status": "PUBLISHED",
            "disease_codes": ["D"],
            "pathology_codes": ["P"],
            "relation": "CURRENT_DISEASE",
            "description": "合同适用关系",
        },
        evidence=[
            {
                "ref": REF,
                "source_code": source,
                "scope": "REGIMEN",
                "status": "PUBLISHED",
                "applicable": True,
                "excerpt": "合同依据",
            }
        ],
    )


def test_levels_and_risk_count_do_not_change_order():
    first, second = plan("B", "FDA"), plan("A", "CSCO")
    first.requirements = [FactRequirement(fact_code="absent")]
    result = assess(snapshot(), [second, first], NOW)
    assert [c.regimen_code for c in result.candidates] == ["B", "A"]
    assert [c.evidence_level for c in result.candidates] == [5, 7]
    assert result.candidates[0].data_labels[0].code == "DATA_UNCONFIRMED"


def test_stage_line_molecular_do_not_filter_or_sort():
    patient = snapshot()
    baseline = assess(patient, [plan()], NOW)
    for key in ("stage", "line", "molecular"):
        patient.facts[key] = Fact(code=key, value="DIFFERENT", status="CONFIRMED", source=REF)
    changed = assess(patient, [plan()], NOW)
    assert [x.regimen_code for x in baseline.candidates] == [
        x.regimen_code for x in changed.candidates
    ]
    assert changed.candidates[0].presentation_region == "RECOMMENDATION"


def test_unrelated_disease_and_confirmed_conflicting_pathology_not_shown():
    p = plan()
    p.applicability.disease_codes = ["OTHER"]
    assert assess(snapshot(), [p], NOW).candidates == []


def test_unreleased_relation_never_becomes_current_disease_recommendation():
    p = plan()
    p.applicability.status = "DRAFT"
    p.applicability.relation = "RELATED_OFF_LABEL"
    result = assess(snapshot(), [p], NOW)
    assert result.candidates == []
    assert result.outcome_code == "NEEDS_REVIEW"


def test_unreleased_clinical_template_does_not_mean_no_appropriate_treatment():
    p = plan()
    p.template_status = "DRAFT"
    assert assess(snapshot(), [p], NOW, "CLINICAL").outcome_code == "NEEDS_REVIEW"
    p.applicability.disease_codes = ["D"]
    p.applicability.pathology_codes = ["OTHER"]
    assert assess(snapshot(), [p], NOW).candidates == []


def test_unknown_pathology_kept_with_notice():
    patient = snapshot()
    del patient.facts["pathology"]
    result = assess(patient, [plan()], NOW)
    assert result.candidates[0].data_labels[0].code == "PATHOLOGY_UNCONFIRMED"


def test_no_disease_returns_needs_input_not_empty_clinical_result():
    patient = snapshot()
    del patient.facts["disease"]
    assert assess(patient, [plan()], NOW).outcome_code == "NEEDS_INPUT"


def test_single_drug_evidence_cannot_grade_entire_combination():
    p = plan()
    p.evidence[0].scope = "DRUG_ONLY"
    result = assess(snapshot(), [p], NOW)
    assert result.candidates[0].evidence_level is None
    assert result.candidates[0].evidence_state == "NO_APPROVED_EVIDENCE"


def test_draft_is_test_reference_never_verified():
    p = plan()
    p.evidence[0].status = "DRAFT"
    result = assess(snapshot(), [p], NOW)
    assert result.outcome_code == "NEEDS_REVIEW"
    assert result.candidates[0].evidence_state == "TEST_REFERENCE"
    assert assess(snapshot(), [p], NOW, "CLINICAL").candidates[0].evidence_level is None


def test_x_requires_confirmed_fact_responsible_drug_and_published_source():
    p = plan()
    p.rules = []
    payload = p.model_dump()
    payload["rules"] = [
        {
            "ref": REF.model_dump(),
            "kind": "ABSOLUTE_CONTRAINDICATION",
            "status": "PUBLISHED",
            "condition": {"fact_code": "contra", "operator": "eq", "value": True},
            "message": "合同禁忌",
            "responsible_drug": "合同药物",
            "source_excerpt": "明确不得使用的合同依据",
        }
    ]
    p = CandidateInput.model_validate(payload)
    patient = snapshot()
    assert assess(patient, [p], NOW).candidates[0].presentation_region == "RECOMMENDATION"
    patient.facts["contra"] = Fact(code="contra", value=True, status="CONFIRMED", source=REF)
    result = assess(patient, [p], NOW)
    assert result.candidates[0].x_reason_code == "ABSOLUTE_CONTRAINDICATION"
    assert result.candidates[0].x_basis
    p.rules[0].responsible_drug = None
    assert assess(patient, [p], NOW).candidates[0].presentation_region == "RECOMMENDATION"


def test_ties_are_stable_and_do_not_add_evidence_counts():
    a, b = plan("A"), plan("B")
    b.evidence = b.evidence * 5
    assert [c.regimen_code for c in assess(snapshot(), [b, a], NOW).candidates] == ["A", "B"]


def test_bsa_retains_unrounded_value():
    result = bsa(snapshot(), NOW, formula_approved=True, max_age_days=30)
    assert result.value == pytest.approx((170 * 60 / 3600) ** 0.5)
    assert result.display_value == "1.68"
    dose = standard_dose(10, "mg/m2", snapshot(), NOW, formula_approved=True, max_age_days=30)
    assert dose.value == pytest.approx(result.value * 10)


@pytest.mark.parametrize("status", ["CONFLICT", "UNCERTAIN", "NEGATED", "MISSING"])
def test_uncertain_input_never_computed(status):
    patient = snapshot()
    patient.facts["weight"].status = status
    assert bsa(patient, NOW, formula_approved=True, max_age_days=30).state == "NOT_COMPUTABLE"


def test_expired_and_future_measurement_not_used():
    for time in (NOW - timedelta(days=31), NOW + timedelta(days=1)):
        patient = deepcopy(snapshot())
        patient.facts["weight"].observed_at = time
        assert bsa(patient, NOW, formula_approved=True, max_age_days=30).state == "NOT_COMPUTABLE"


def test_crcl_cannot_substitute_gfr_or_indexed_gfr():
    patient = snapshot()
    patient.facts["crcl"] = Fact(
        code="crcl", value=50.0, unit="mL/min", observed_at=NOW, status="CONFIRMED", source=REF
    )
    assert (
        standard_dose(5, "AUC", patient, NOW, formula_approved=True, max_age_days=7).state
        == "NOT_COMPUTABLE"
    )
    patient.facts["gfr"] = Fact(
        code="gfr",
        value=50.0,
        unit="mL/min/1.73m2",
        observed_at=NOW,
        status="CONFIRMED",
        source=REF,
    )
    assert (
        standard_dose(5, "AUC", patient, NOW, formula_approved=True, max_age_days=7).state
        == "NOT_COMPUTABLE"
    )


def test_formula_needs_explicit_approval():
    assert bsa(snapshot(), NOW, formula_approved=False, max_age_days=30).state == "NOT_COMPUTABLE"
    assert (
        standard_dose(1, "mg", snapshot(), NOW, formula_approved=False, max_age_days=30).state
        == "NOT_COMPUTABLE"
    )
