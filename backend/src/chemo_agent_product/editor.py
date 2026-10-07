"""The same fixed Word form is viewed and edited; public templates remain unchanged."""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, time
from typing import Any

from chemo_agent_product.calculations import bsa, standard_dose
from chemo_agent_product.domain import PatientSnapshot
from chemo_agent_product.patient_contracts import SaveInput
from chemo_agent_product.security import BusinessError


def checked_value(field: dict, value: Any):
    if value is None or value == "":
        return None
    kind = field["value_type"]
    if kind in {"number", "integer"}:
        if isinstance(value, bool):
            raise ValueError("boolean is not a clinical number")
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError("invalid number")
        if kind == "integer":
            if not number.is_integer():
                raise ValueError("integer required")
            if field["field_key"] in {"current_cycle", "total_cycles"} and number < 1:
                raise ValueError("positive cycle required")
            return int(number)
        return number
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError("invalid text")
    if kind == "date":
        date.fromisoformat(value)
    if kind == "time":
        time.fromisoformat(value)
    return value.strip()


def compile_revision(
    template: dict,
    snapshot: PatientSnapshot,
    request: SaveInput,
    initial_fields: dict,
    calculation_policy: dict | None = None,
):
    definitions = {f["field_key"]: f for f in template["fields"]}
    values = dict(initial_fields)
    for key, value in request.field_values.items():
        field = definitions.get(key)
        if not field or field["edit_policy"] != "RUNTIME_EDITABLE":
            raise BusinessError("FIELD_NOT_EDITABLE", "请求包含不可编辑的表单字段", 422)
        try:
            values[key] = checked_value(field, value)
        except (ValueError, TypeError, OverflowError):
            raise BusinessError(
                "FIELD_VALUE_INVALID", f"请核对“{field['label']}”的填写格式", 422
            ) from None
    if (
        values.get("current_cycle")
        and values.get("total_cycles")
        and values["current_cycle"] > values["total_cycles"]
    ):
        raise BusinessError("CYCLE_RANGE_INVALID", "当前周期不能大于总周期数", 422)
    meds = {m["item_key"]: m for m in template["medications"]}
    if set(request.medication_values) - set(meds):
        raise BusinessError("MEDICATION_NOT_IN_TEMPLATE", "药物不属于固定来源方案", 422)
    issues = []
    if not any(f.get("required") for f in definitions.values()):
        issues.append({"code": "REQUIRED_FIELDS_UNCONFIGURED", "message": "必填项配置尚未完成"})
    for key, field in definitions.items():
        if field.get("required") and values.get(key) in (None, ""):
            issues.append(
                {
                    "code": f"FIELD_MISSING:{key}",
                    "message": f"请填写{field['label']}",
                    "field_path": key,
                }
            )
    policy = calculation_policy or {}
    max_age = policy.get("measurement_max_age_days")
    approved = policy.get("bsa_formula_approved") is True and type(max_age) is int and max_age >= 0
    calculation = bsa(snapshot, datetime.now(UTC), formula_approved=approved, max_age_days=max_age)
    if calculation.state == "COMPUTED":
        values["body_surface_area_m2"] = calculation.value
    reference_doses = {}
    orders = []
    medication_values = {
        key: value.model_dump(mode="json") for key, value in request.medication_values.items()
    }
    for index, med in enumerate(template["medications"], 1):
        edit = medication_values.get(med["item_key"], {})
        dose = edit.get("actual_dose_text", "").strip()
        numeric = re.match(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?=$|[^\d.])", dose)
        if (numeric and float(numeric[1]) <= 0) or re.match(
            r"^[+-]?(?:nan|inf(?:inity)?)(?:\s|$)", dose, re.IGNORECASE
        ):
            raise BusinessError("DOSE_INVALID", "实际剂量必须为正数", 422)
        day = edit.get("administration_day_text") or med.get("administration_day_text")
        dose_reference = None
        base = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(mg/m[²2]|mg/kg|mg)\s*",
            med.get("standard_dose_text") or "",
            re.IGNORECASE,
        )
        if base:
            unit = {"mg/m²": "mg/m2"}.get(base[2], base[2])
            dose_reference = standard_dose(
                float(base[1]),
                unit,
                snapshot,
                datetime.now(UTC),
                formula_approved=approved,
                max_age_days=max_age,
            ).model_dump(mode="json")
        reference_doses[med["item_key"]] = dose_reference or {
            "state": "NOT_COMPUTABLE",
            "reasons": [
                {
                    "code": "DOSE_EXPRESSION_REQUIRES_REVIEW",
                    "message": "剂量表达含范围、阶段或特殊条件，需核对结构化配置",
                }
            ],
        }
        if not dose:
            issues.append(
                {
                    "code": f"DOSE_MISSING:{med['item_key']}",
                    "message": f"{med['source_drug_name']}的实际剂量尚未填写",
                }
            )
        if not med.get("frequency_text"):
            issues.append(
                {
                    "code": f"FREQUENCY_PENDING:{med['item_key']}",
                    "message": f"{med['source_drug_name']}的结构化频次待配置；原文给药安排保留",
                }
            )
        orders.append(
            dict(
                line_no=index,
                order_category="CHEMOTHERAPY",
                item_type="DRUG",
                source_locator={
                    "item_key": med["item_key"],
                    "template_version_id": template["version_id"],
                },
                item_name=med["source_drug_name"],
                dose_text_raw=dose or None,
                schedule_text_raw=day,
                special_instructions=edit.get("instructions") or None,
                resolution_state="UNMAPPED" if dose else "MISSING_FIELD",
            )
        )
    issues.append(
        {
            "code": "HOSPITAL_CODES_UNCONFIGURED",
            "message": "院方药品、途径和频次映射尚未完成，不能交付医嘱",
        }
    )
    manifest = {
        "medication_values": medication_values,
        "issues": issues,
        "bsa": calculation.model_dump(mode="json"),
        "reference_doses": reference_doses,
        "calculation_policy": policy,
    }
    return {"fields": values, "orders": orders, "issues": issues, "manifest": manifest}
