"""Entirely fictional hospital v1.0.1 fixtures, never a clinical rule publication."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from chemo_agent_product.hospital import READ_OPERATIONS

PATIENTS = {
    "P261008001": {
        "name": "林雅琴",
        "birth_date": "1974-05-12",
        "disease": "乳腺肿瘤",
        "diagnosis": "乳腺恶性肿瘤",
        "pathology": "浸润性乳腺癌",
        "purpose": "新辅助治疗",
        "height": 165,
        "weight": 60,
        "description": "乳腺恶性肿瘤 · 新辅助治疗",
        "case": "normal",
    },
    "P261008002": {
        "name": "陈建华",
        "birth_date": "1963-11-08",
        "disease": "消化系统肿瘤",
        "diagnosis": "胃恶性肿瘤",
        "pathology": "胃腺癌",
        "purpose": "辅助治疗",
        "height": 172,
        "weight": 68,
        "description": "胃恶性肿瘤 · 辅助治疗",
        "case": "normal",
    },
    "P261008003": {
        "name": "王丽芳",
        "birth_date": "1978-03-26",
        "disease": "乳腺肿瘤",
        "diagnosis": "乳腺恶性肿瘤",
        "pathology": "浸润性乳腺癌",
        "purpose": "辅助治疗",
        "height": 160,
        "weight": None,
        "description": "乳腺恶性肿瘤 · 检验待更新",
        "case": "missing",
    },
    "P261008004": {
        "name": "周晓梅",
        "birth_date": "1968-09-17",
        "disease": "乳腺肿瘤",
        "diagnosis": "乳腺恶性肿瘤",
        "pathology": "浸润性乳腺癌",
        "purpose": "新辅助治疗",
        "height": 168,
        "weight": 64,
        "description": "乳腺恶性肿瘤 · 医嘱待处理",
        "case": "partial",
    },
}
DOCTORS = {
    "responsible_doctor_id": "DR1001",
    "responsible_doctor_name": "李明远",
    "attending_physician_id": "DR1002",
    "attending_physician_name": "张文博",
    "deputy_chief_physician_id": "DR1003",
    "deputy_chief_physician_name": "陈静宜",
    "chief_physician_id": None,
    "chief_physician_name": None,
    "doctor_phone": None,
}

# Existing receipts keep their original patient identities after a presentation update.
LEGACY_PATIENTS = {"DEMO_P" + str(i).zfill(3): info for i, info in enumerate(PATIENTS.values(), 1)}


def encounter_matches(patient, encounter):
    return patient in PATIENTS and encounter.startswith("IP261008" + patient[-3:])


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
            "birth_date": info["birth_date"],
            "medical_record_no": "MR261008" + patient[-3:],
            "visit_type": "INPATIENT",
            "dept_code": "ONC01",
            "dept_name": "肿瘤内科",
            "ward_code": "ONC_W12",
            "ward_name": "肿瘤内科十二病区",
            "bed_no": patient[-2:],
            "encounter_status": "IN_PROGRESS",
            **DOCTORS,
        }
    elif name == "Q_GetPatientClinicalData":
        content = {
            "diagnosis_list": [
                {
                    "diagnosis_code": "C50.9" if info["disease"] == "乳腺肿瘤" else "C16.9",
                    "diagnosis_name": info["diagnosis"],
                    "primary_flag": True,
                }
            ],
            "pathology_diagnosis": info["pathology"],
            "primary_site": {
                "code": "BREAST" if info["disease"] == "乳腺肿瘤" else "STOMACH",
                "name": info["disease"],
            },
            "tnm_stage": "III期",
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
                "report_id": "LAB261008" + patient[-3:],
                "report_no": "LAB261008" + patient[-3:],
                "report_type": "LAB",
                "report_name": "血常规与肝肾功能",
                "collect_time": stamp(collected),
                "report_time": stamp(collected),
                "audit_status": "AUDITED",
                "items": items,
            }
        ]
    elif name == "Q_GetClinicalRecord":
        content = [
            {
                "document_id": "CR261008" + patient[-3:],
                "document_type": "COURSE_RECORD",
                "document_title": "入院病程记录",
                "author_id": "DR1001",
                "document_time": stamp(),
                "document_content": (
                    f"{info['name']}，{info['diagnosis']}，{info['pathology']}，"
                    f"{info['purpose']}。未见明确药物过敏。"
                ),
                "document_summary": f"{info['diagnosis']}，拟行{info['purpose']}。",
                "sign_status": "SIGNED",
            }
        ]
    elif name == "Q_GetMedicalStaffInfo":
        content = {
            "staff_id": "DR1001",
            "staff_name": "李明远",
            "staff_role_list": ["RESPONSIBLE_DOCTOR", "OPERATOR"],
            "dept_code": "ONC01",
            "dept_name": "肿瘤内科",
            "signature_id": "ESIGN_DR1001",
            "signature_status": "AVAILABLE",
            "updated_time": stamp(),
        }
    elif name == "Q_GetOrderDictionary":
        content = dictionary
    else:
        content = []  # No historical medication or operation inferred from the templates.
    return {
        "code": "0",
        "msg": "查询成功",
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
        "hospital_code": "H1001",
        "credential_environment_variable": "CHEMO_DEMO_HOSPITAL_AUTH",
        "operations": {},
        "facts": {},
        "text_records": [],
    }
    for name in sorted(READ_OPERATIONS):
        profile["operations"][name] = {
            "url": f"{origin}/workstation/hospital/{name}",
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
