"""Entirely fictional hospital v1.0.1 fixtures, never a clinical rule publication."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from chemo_agent_product.hospital import READ_OPERATIONS

PATIENTS = {
    "DEMO_P001": {
        "name": "演示患者甲",
        "disease": "乳腺肿瘤",
        "diagnosis": "乳腺恶性肿瘤",
        "pathology": "浸润性乳腺癌",
        "purpose": "新辅助治疗",
        "height": 165,
        "weight": 60,
        "description": "完整资料 · 对比、编辑、确认及归档",
        "case": "normal",
    },
    "DEMO_P002": {
        "name": "演示患者乙",
        "disease": "消化系统肿瘤",
        "diagnosis": "胃恶性肿瘤",
        "pathology": "胃腺癌",
        "purpose": "辅助治疗",
        "height": 172,
        "weight": 68,
        "description": "另一癌种 · 切患者隔离及历史恢复",
        "case": "normal",
    },
    "DEMO_P003": {
        "name": "演示患者丙",
        "disease": "乳腺肿瘤",
        "diagnosis": "乳腺恶性肿瘤",
        "pathology": "浸润性乳腺癌",
        "purpose": "辅助治疗",
        "height": 160,
        "weight": None,
        "description": "缺体重、缺ANC、检验过期 · 保留候选与提示",
        "case": "missing",
    },
    "DEMO_P004": {
        "name": "演示患者丁",
        "disease": "乳腺肿瘤",
        "diagnosis": "乳腺恶性肿瘤",
        "pathology": "浸润性乳腺癌",
        "purpose": "新辅助治疗",
        "height": 168,
        "weight": 64,
        "description": "医嘱部分失败 · 回查、撤销与不可重复归档",
        "case": "partial",
    },
}
DOCTORS = {
    "responsible_doctor_id": "DEMO_DOC01",
    "responsible_doctor_name": "演示主管医师",
    "attending_physician_id": "DEMO_DOC02",
    "attending_physician_name": "演示主治医师",
    "deputy_chief_physician_id": "DEMO_DOC03",
    "deputy_chief_physician_name": "演示副主任医师",
    "chief_physician_id": None,
    "chief_physician_name": None,
    "doctor_phone": None,
}


def stamp(value=None):
    return (
        (value or datetime.now(UTC))
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .strftime("%Y%m%d%H%M%S%f")[:-3]
    )


def hospital_read(name, body, dictionary):
    patient = body.get("patient_id")
    info = PATIENTS[patient]
    now = datetime.now(UTC)
    collected = now - timedelta(days=12 if info["case"] == "missing" else 1)
    encounter = body["encounter_id"]
    if name == "Q_GetPatientEncounter":
        content = {
            "patient_id": patient,
            "encounter_id": encounter,
            "patient_name": info["name"],
            "gender": "F" if info["disease"] == "乳腺肿瘤" else "M",
            "birth_date": "1975-06-15",
            "medical_record_no": "DEMO_MR" + patient[-3:],
            "visit_type": "INPATIENT",
            "dept_code": "DEMO_ONC",
            "dept_name": "演示肿瘤科",
            "ward_code": "DEMO_W01",
            "ward_name": "演示病区",
            "bed_no": patient[-2:],
            "encounter_status": "IN_PROGRESS",
            **DOCTORS,
        }
    elif name == "Q_GetPatientClinicalData":
        content = {
            "diagnosis_list": [
                {
                    "diagnosis_code": "DEMO_CANCER",
                    "diagnosis_name": info["diagnosis"],
                    "primary_flag": True,
                }
            ],
            "pathology_diagnosis": info["pathology"],
            "primary_site": {"code": "DEMO_SITE", "name": info["disease"]},
            "tnm_stage": "演示分期 III期",
            "treatment_purpose": info["purpose"],
            "treatment_line": "一线",
            "ecog_score": 1,
            "height_cm": info["height"],
            "weight_kg": info["weight"],
            "record_time": stamp(now),
            "infection_status": "NONE",
            "bleeding_risk": "LOW",
            "molecular_marker_list": [{"marker_code": "HER2", "result": "3+"}],
            "allergy_list": [],
            "previous_treatment_list": [],
        }
    elif name == "Q_GetClinicalReport":
        items = [
            {
                "item_code": code,
                "item_name": code,
                "result_value": value,
                "result_unit": unit,
                "abnormal_flag": "N",
            }
            for code, value, unit in [
                ("WBC", 5.8, "10^9/L"),
                ("ANC", None if info["case"] == "missing" else 3.2, "10^9/L"),
                ("PLT", 220, "10^9/L"),
                ("ALT", 22, "U/L"),
                ("AST", 24, "U/L"),
                ("TBIL", 12, "umol/L"),
                ("CREA", 72, "umol/L"),
                ("GFR", 90, "mL/min"),
                ("HB", 125, "g/L"),
                ("LVEF", 63, "%"),
            ]
        ]
        content = [
            {
                "report_id": "DEMO_LAB" + patient[-3:],
                "report_no": "DEMO_LAB" + patient[-3:],
                "report_type": "LAB",
                "report_name": "演示血常规与肝肾功能",
                "collect_time": stamp(collected),
                "report_time": stamp(collected),
                "audit_status": "AUDITED",
                "items": items,
            }
        ]
    elif name == "Q_GetClinicalRecord":
        content = [
            {
                "document_id": "DEMO_DOC" + patient[-3:],
                "document_type": "COURSE_RECORD",
                "document_title": "演示病程",
                "author_id": "DEMO_DOC01",
                "document_time": stamp(),
                "document_content": (
                    f"【虚构演示】{info['diagnosis']}，{info['pathology']}，"
                    f"{info['purpose']}。未见明确药物过敏。"
                ),
                "document_summary": "完全虚构的流程演示数据",
                "sign_status": "SIGNED",
            }
        ]
    elif name == "Q_GetMedicalStaffInfo":
        content = {
            "staff_id": "DEMO_DOC01",
            "staff_name": "演示主管医师",
            "staff_role_list": ["RESPONSIBLE_DOCTOR", "OPERATOR"],
            "dept_code": "DEMO_ONC",
            "dept_name": "演示肿瘤科",
            "signature_id": "DEMO_SIGNATURE_RESOURCE",
            "signature_status": "AVAILABLE",
            "updated_time": stamp(),
        }
    elif name == "Q_GetOrderDictionary":
        content = dictionary
    else:
        content = []  # No historical medication or operation inferred from the templates.
    return {
        "code": "0",
        "msg": "模拟医院返回",
        "demo": True,
        "data": {
            "content": content,
            "page": 1,
            "size": max(1, len(content)) if isinstance(content, list) else 1,
            "total": len(content) if isinstance(content, list) else 1,
        },
    }


def read_profile(origin):
    profile = {
        "schema_version": "hospital-adapter.v1",
        "contract_version": "v1.0.1",
        "hospital_code": "DEMO_HOSPITAL",
        "credential_environment_variable": "CHEMO_DEMO_HOSPITAL_AUTH",
        "operations": {},
        "facts": {},
        "text_records": [],
    }
    for name in sorted(READ_OPERATIONS):
        profile["operations"][name] = {
            "url": f"{origin}/demo/hospital/{name}",
            "identity_policy": "REQUEST_SCOPE",
        }
    profile["operations"]["Q_GetPatientEncounter"].update(
        {
            "identity_policy": "RESPONSE",
            "patient_pointer": "/data/content/patient_id",
            "encounter_pointer": "/data/content/encounter_id",
        }
    )

    def field(code, op, path, **extra):
        profile["facts"][code] = {"operation": op, "pointer": "/data/content" + path, **extra}

    encounter = "Q_GetPatientEncounter"
    clinical = "Q_GetPatientClinicalData"
    for code, key in {
        "patient_name": "patient_name",
        "birth_date": "birth_date",
        "medical_record_number": "medical_record_no",
        **{k: k for k in DOCTORS if k != "doctor_phone"},
    }.items():
        field(code, encounter, "/" + key)
    for key, source in {
        "supervising_physician": "responsible_doctor_name",
        "attending_physician": "attending_physician_name",
        "associate_chief_physician": "deputy_chief_physician_name",
        "chief_physician": "chief_physician_name",
    }.items():
        field(key, encounter, "/" + source)
    field("disease", clinical, "/primary_site/name")
    field("diagnosis", clinical, "/diagnosis_list/0/diagnosis_name")
    field("pathology", clinical, "/pathology_diagnosis")
    field("ecog", clinical, "/ecog_score")
    field("her2_status", clinical, "/molecular_marker_list/0/result")
    for code, key, unit in [("height", "height_cm", "cm"), ("weight", "weight_kg", "kg")]:
        field(code, clinical, "/" + key, unit=unit, observed_at_pointer="/data/content/record_time")
    for i, code in enumerate(
        [
            "wbc",
            "anc",
            "platelets",
            "alt",
            "ast",
            "total_bilirubin",
            "creatinine",
            "gfr",
            "hemoglobin_hematocrit",
            "lvef",
        ]
    ):
        field(
            code,
            "Q_GetClinicalReport",
            f"/0/items/{i}/result_value",
            unit_pointer=f"/data/content/0/items/{i}/result_unit",
            observed_at_pointer="/data/content/0/collect_time",
            status_pointer="/data/content/0/audit_status",
            status_map={"AUDITED": "CONFIRMED"},
        )
    profile["text_records"] = [
        {
            "operation": "Q_GetClinicalRecord",
            "records_pointer": "/data/content",
            "text_pointer": "/document_content",
            "record_id_pointer": "/document_id",
            "category": "COURSE_RECORD",
        }
    ]
    return profile
