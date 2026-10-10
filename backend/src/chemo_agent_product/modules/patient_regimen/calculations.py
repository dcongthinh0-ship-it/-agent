"""Standard reference calculations, never automatic final prescriptions."""

from __future__ import annotations

import math
from datetime import datetime

from chemo_agent_product.core.domain import (
    Calculation,
    FactRequirement,
    Finding,
    PatientSnapshot,
    usable_fact,
)


def bsa(
    snapshot: PatientSnapshot, now: datetime, *, formula_approved: bool, max_age_days: int
) -> Calculation:
    result = Calculation(formula_version="Mosteller.v1", state="NOT_COMPUTABLE", unit="m2")
    if not formula_approved:
        result.reasons.append(Finding(code="FORMULA_UNAPPROVED", message="公式配置尚未确认"))
        return result
    inputs = {}
    for code, unit in (("height", "cm"), ("weight", "kg")):
        fact, issue = usable_fact(
            snapshot, FactRequirement(fact_code=code, unit=unit, max_age_days=max_age_days), now
        )
        if issue:
            result.reasons.append(issue)
        elif fact:
            if (
                isinstance(fact.value, bool)
                or not isinstance(fact.value, (float, int))
                or not math.isfinite(fact.value)
                or fact.value <= 0
            ):
                result.reasons.append(
                    Finding(
                        code="INVALID_MEASUREMENT",
                        message="身高体重数值无效",
                        field_path=f"facts.{code}",
                    )
                )
            else:
                inputs[code] = fact.value
                result.source_refs.append(fact.source)
    result.inputs = inputs
    if result.reasons:
        return result
    result.value = math.sqrt(inputs["height"] * inputs["weight"] / 3600)
    result.display_value = f"{result.value:.2f}"
    result.state = "COMPUTED"
    return result


def standard_dose(
    base: float,
    basis: str,
    snapshot: PatientSnapshot,
    now: datetime,
    *,
    formula_approved: bool,
    max_age_days: int,
) -> Calculation:
    result = Calculation(
        formula_version="standard-dose.v1",
        state="NOT_COMPUTABLE",
        unit="mg",
        inputs={"base": base, "basis": basis},
    )
    if not formula_approved:
        result.reasons.append(Finding(code="FORMULA_UNAPPROVED", message="剂量规则尚未确认"))
        return result
    if not math.isfinite(base) or base <= 0:
        result.reasons.append(Finding(code="DOSE_BASE_INVALID", message="标准剂量基数无效"))
        return result
    if basis == "mg/m2":
        surface = bsa(snapshot, now, formula_approved=True, max_age_days=max_age_days)
        result.reasons = surface.reasons
        result.source_refs = surface.source_refs
        if surface.value is None:
            return result
        multiplier = surface.value
        result.inputs["bsa_unrounded"] = multiplier
    elif basis in {"mg/kg", "AUC"}:
        # GFR and CrCl are deliberately distinct. No automatic substitution.
        code, unit = ("weight", "kg") if basis == "mg/kg" else ("gfr", "mL/min")
        fact, issue = usable_fact(
            snapshot, FactRequirement(fact_code=code, unit=unit, max_age_days=max_age_days), now
        )
        if issue:
            result.reasons.append(issue)
            return result
        assert fact is not None
        if (
            isinstance(fact.value, bool)
            or not isinstance(fact.value, (float, int))
            or not math.isfinite(fact.value)
            or fact.value <= 0
        ):
            result.reasons.append(Finding(code="INVALID_MEASUREMENT", message="计算输入无效"))
            return result
        result.source_refs = [fact.source]
        result.inputs[code] = fact.value
        multiplier = fact.value if basis == "mg/kg" else fact.value + 25
    elif basis == "mg":
        multiplier = 1
    else:
        result.reasons.append(
            Finding(code="DOSE_BASIS_UNSUPPORTED", message="剂量基数类型尚未支持")
        )
        return result
    result.value = base * multiplier
    result.display_value = f"{result.value:.2f}"
    result.state = "COMPUTED"
    return result
