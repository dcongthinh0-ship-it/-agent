"""V2.2 deterministic matching. Agent output cannot change these results."""

from __future__ import annotations

from datetime import datetime

from chemo_agent_product.domain import (
    CandidateAssessment,
    CandidateInput,
    ClinicalRule,
    DecisionResult,
    FactRequirement,
    Finding,
    PatientSnapshot,
    usable_fact,
)

LEVEL_MAPPING_VERSION = "20260930-FDA5-CSCO7-v1"
LEVELS = {"FDA": 5, "DAILYMED": 5, "CSCO": 7}


def rule_matches(
    rule: ClinicalRule, snapshot: PatientSnapshot, now: datetime
) -> tuple[bool, Finding | None]:
    condition = rule.condition
    fact, issue = usable_fact(
        snapshot,
        FactRequirement(
            fact_code=condition.fact_code, unit=condition.unit, max_age_days=condition.max_age_days
        ),
        now,
    )
    if issue:
        return False, issue
    assert fact is not None
    value = fact.value
    expected = condition.value
    if condition.operator == "eq":
        return type(value) is type(expected) and value == expected, None
    if condition.operator == "in":
        return isinstance(expected, list) and value in expected, None
    if (
        isinstance(value, bool)
        or isinstance(expected, bool)
        or not isinstance(value, (float, int))
        or not isinstance(expected, (float, int))
    ):
        return False, Finding(
            code="RULE_VALUE_INVALID",
            message="规则输入类型不匹配",
            field_path=f"facts.{condition.fact_code}",
        )
    operations = {
        "lt": value < expected,
        "lte": value <= expected,
        "gt": value > expected,
        "gte": value >= expected,
    }
    return operations[condition.operator], None


def assess(
    snapshot: PatientSnapshot,
    plans: list[CandidateInput],
    now: datetime,
    usage_mode: str = "TEST_ONLY",
) -> DecisionResult:
    if usage_mode not in {"TEST_ONLY", "CLINICAL"}:
        raise ValueError("invalid usage mode")
    disease, issue = usable_fact(snapshot, FactRequirement(fact_code="disease"), now)
    if issue or disease is None:
        return DecisionResult(
            usage_mode=usage_mode,
            outcome_code="NEEDS_INPUT",
            candidates=[],
            notices=[issue] if issue else [],
        )
    pathology = snapshot.facts.get("pathology")
    candidates = []
    notices = []
    for plan in plans:
        relation = plan.applicability
        if relation.status == "RETIRED" or plan.template_status == "RETIRED":
            continue
        if disease.value not in relation.disease_codes:
            continue
        if (
            usage_mode == "CLINICAL"
            and (relation.status != "PUBLISHED" or plan.template_status != "PUBLISHED")
        ) or (relation.relation == "RELATED_OFF_LABEL" and relation.status != "PUBLISHED"):
            notices.append(
                Finding(
                    code="KNOWLEDGE_RELEASE_REQUIRED",
                    message="相关方案或适用关系尚待核验",
                    source_refs=[relation.ref],
                    details={"regimen_code": plan.regimen_code},
                )
            )
            continue
        data = []
        if not relation.pathology_unrestricted:
            if not relation.pathology_codes:
                data.append(
                    Finding(code="PATHOLOGY_SCOPE_UNCONFIGURED", message="方案病理适用范围尚待核验")
                )
            elif pathology is None or pathology.status != "CONFIRMED" or pathology.value is None:
                data.append(Finding(code="PATHOLOGY_UNCONFIRMED", message="病理信息尚待确认"))
            elif pathology.value not in relation.pathology_codes:
                continue
        for requirement in plan.requirements:
            _, notice = usable_fact(snapshot, requirement, now)
            if notice:
                data.append(notice)
        risks = []
        x_basis = []
        x_reason = None
        if relation.relation == "RELATED_OFF_LABEL":
            # This relationship must come from configuration, never model guesses.
            if relation.status != "PUBLISHED":
                data.append(Finding(code="RELATION_UNAPPROVED", message="关联适用关系尚未核验"))
            else:
                x_reason = "RELATED_OFF_LABEL"
                x_basis.append({"relation": relation.model_dump(mode="json")})
        for rule in plan.rules:
            if rule.status != "PUBLISHED":
                continue
            matched, notice = rule_matches(rule, snapshot, now)
            if notice:
                data.append(notice)
            if matched:
                if rule.kind == "ABSOLUTE_CONTRAINDICATION":
                    if not rule.source_excerpt.strip() or not rule.responsible_drug:
                        data.append(Finding(code="X_BASIS_INCOMPLETE", message="禁忌依据不完整"))
                        continue
                    x_reason = "ABSOLUTE_CONTRAINDICATION"
                    x_basis.append(
                        {
                            "rule": rule.model_dump(mode="json"),
                            "fact": snapshot.facts[rule.condition.fact_code].model_dump(
                                mode="json"
                            ),
                        }
                    )
                else:
                    risks.append(
                        Finding(
                            code="SAFETY_NOTICE",
                            message=rule.message,
                            field_path=f"facts.{rule.condition.fact_code}",
                            patient_value=snapshot.facts[rule.condition.fact_code].value,
                            source_refs=[rule.ref],
                        )
                    )
        eligible = [
            e
            for e in plan.evidence
            if e.applicable
            and e.scope == "REGIMEN"
            and e.source_code.upper() in LEVELS
            and e.status != "RETIRED"
        ]
        approved = [e for e in eligible if e.status == "PUBLISHED"]
        selected = min(
            approved or (eligible if usage_mode == "TEST_ONLY" else []),
            key=lambda e: (LEVELS[e.source_code.upper()], e.ref.id),
            default=None,
        )
        state = (
            "VERIFIED"
            if selected and selected.status == "PUBLISHED"
            else "TEST_REFERENCE"
            if selected
            else "NO_APPROVED_EVIDENCE"
        )
        if relation.status != "PUBLISHED":
            data.append(Finding(code="APPLICABILITY_UNVERIFIED", message="适用关系待核验"))
        if state != "VERIFIED":
            data.append(Finding(code="EVIDENCE_UNVERIFIED", message="证据尚未完成适用核验"))
        candidates.append(
            CandidateAssessment(
                regimen_id=plan.regimen_id,
                version_id=plan.version_id,
                regimen_code=plan.regimen_code,
                display_name=plan.display_name,
                presentation_region="X_EXCLUDED" if x_reason else "RECOMMENDATION",
                evidence_state=state,
                evidence_level=LEVELS[selected.source_code.upper()] if selected else None,
                evidence_grade="I-A" if selected else None,
                selected_evidence_ref=selected.ref if selected else None,
                evidence_refs=[e.ref for e in plan.evidence],
                data_labels=data,
                safety_labels=risks,
                x_reason_code=x_reason,
                x_basis=x_basis,
                applicability=relation,
            )
        )
    # Stable key breaks ties only. No risk, stage, line, missingness, or count weights.
    candidates.sort(
        key=lambda c: (
            c.presentation_region == "X_EXCLUDED",
            c.evidence_level or 99,
            c.regimen_code,
            str(c.version_id),
        )
    )
    outcome = "CANDIDATES" if candidates else "NEEDS_REVIEW" if notices else "NO_CANDIDATE"
    if candidates and (
        all(c.evidence_state != "VERIFIED" for c in candidates)
        or any(c.applicability.status != "PUBLISHED" for c in candidates)
    ):
        outcome = "NEEDS_REVIEW"
    return DecisionResult(
        usage_mode=usage_mode, outcome_code=outcome, candidates=candidates, notices=notices
    )
