from __future__ import annotations

from uuid import UUID

from pydantic import Field

from chemo_agent_product.database import insert
from chemo_agent_product.domain import (
    Applicability,
    CandidateInput,
    ClinicalRule,
    Contract,
    EvidenceReference,
    FactRequirement,
    Reference,
    fingerprint,
)
from chemo_agent_product.security import BusinessError


class RuleEntry(Contract):
    applies_to_versions: list[UUID] = Field(min_length=1)
    rule: ClinicalRule


class RulePayload(Contract):
    requirements: list[FactRequirement] = Field(default_factory=list)
    rules: list[RuleEntry] = Field(default_factory=list)
    calculation: dict = Field(default_factory=dict)


def evidence_link_matches(link: dict, blueprint: dict, medications: list) -> bool:
    """Validate the declared import contract against current source content.

    Reading a matching DRAFT never grants publication or clinical approval.
    Unknown contracts are not accepted as a blueprint-only shortcut.
    """
    contract = link.get("hash_contract_version")
    if contract == "blueprint-and-medication-json-v1":
        expected = fingerprint({"blueprint": blueprint, "medications": medications})
    elif contract == "blueprint-json-v1":
        expected = fingerprint(blueprint)
    else:
        return False
    return link["regimen_content_hash"] == expected


async def load_inputs(c, disease: str, usage_mode: str):
    rows = await c.fetch(
        """SELECT r.id AS regimen_id,r.regimen_code,r.display_name,r.cancer_category,
      v.id AS version_id,v.status,b.document_tree,
      (SELECT coalesce(jsonb_agg(jsonb_build_object(
        'name',m.source_drug_name,'dose',m.standard_dose_text,
        'day',m.administration_day_text,'drug_concept_id',m.drug_concept_id)
        ORDER BY m.display_order),'[]'::jsonb)
        FROM regimen_catalog.regimen_medication_item m WHERE m.regimen_version_id=v.id
      ) AS medication_snapshot FROM regimen_catalog.regimen r
      JOIN regimen_catalog.regimen_version v ON v.regimen_id=r.id
      JOIN regimen_catalog.form_blueprint b ON b.regimen_version_id=v.id
      WHERE r.active AND v.status<>'RETIRED' AND (r.cancer_category=$1 OR EXISTS(
        SELECT 1 FROM knowledge.regimen_applicability_version a WHERE a.regimen_version_id=v.id
        AND a.disease_code=$1 AND a.status<>'RETIRED'))""",
        disease,
    )
    packages = await c.fetch("""SELECT DISTINCT ON(package_key) * FROM knowledge.rule_package_version
      WHERE status='PUBLISHED' ORDER BY package_key,version_no DESC""")
    rules = []
    requirements = []
    manifest = {
        "rule_packages": [],
        "evidence_mapping": "20260930-FDA5-CSCO7-v1",
        "calculation": {},
    }
    for package in packages:
        if package["schema_version"] != "rule-package.v1":
            raise BusinessError("RULE_SCHEMA_UNSUPPORTED", "存在尚未支持的规则包版本", 503)
        try:
            payload = RulePayload.model_validate(package["policy_payload"])
        except ValueError:
            raise BusinessError("RULE_PACKAGE_INVALID", "规则包结构未通过校验", 503) from None
        ref = Reference(
            namespace="knowledge.rule_package_version",
            id=str(package["id"]),
            version=str(package["version_no"]),
            content_hash=package["content_hash"],
        )
        manifest["rule_packages"].append(ref.model_dump(mode="json"))
        if (
            manifest["calculation"]
            and payload.calculation
            and manifest["calculation"] != payload.calculation
        ):
            raise BusinessError("CALCULATION_POLICY_CONFLICT", "计算规则包存在冲突", 503)
        if payload.calculation:
            manifest["calculation"] = payload.calculation
        rules.extend(payload.rules)
        requirements.extend(payload.requirements)
    plans = []
    for row in rows:
        blueprint_hash = fingerprint(row["document_tree"])
        relations = await c.fetch(
            """SELECT DISTINCT ON(applicability_key) *
          FROM knowledge.regimen_applicability_version WHERE regimen_version_id=$1 AND disease_code=$2
          AND status<>'RETIRED' ORDER BY applicability_key,version_no DESC""",
            row["version_id"],
            disease,
        )
        if len(relations) > 1:
            raise BusinessError(
                "APPLICABILITY_AMBIGUOUS", "同一方案存在多个待区分的适用场景，请核对配置", 503
            )
        relation = relations[0] if relations else None
        if relation:
            if relation["regimen_content_hash"] != blueprint_hash:
                continue
            path = relation["pathology_scope"]
            applicable = Applicability(
                ref=Reference(
                    namespace="knowledge.regimen_applicability_version",
                    id=str(relation["id"]),
                    version=str(relation["version_no"]),
                    content_hash=relation["content_hash"],
                ),
                status="PUBLISHED" if relation["status"] == "PUBLISHED" else "DRAFT",
                disease_codes=[relation["disease_code"]],
                pathology_codes=path.get("codes", []),
                pathology_unrestricted=path.get("unrestricted") is True,
                relation="CURRENT_DISEASE"
                if relation["relation_kind"] == "DIRECT"
                else "RELATED_OFF_LABEL",
                description=relation["source_condition_text"],
            )
        elif usage_mode == "TEST_ONLY" and row["cancer_category"] == disease:
            # Exact catalog category only, explicitly unverified; no synonym/pathology or off-label guesses.
            applicable = Applicability(
                ref=Reference(
                    namespace="regimen_catalog.cancer_category",
                    id=str(row["version_id"]),
                    version="catalog-category-test.v1",
                    content_hash=blueprint_hash,
                ),
                status="DRAFT",
                disease_codes=[disease],
                pathology_codes=[],
                pathology_unrestricted=False,
                relation="CURRENT_DISEASE",
                description="目录原始癌种分类，尚未核验适用关系",
            )
        else:
            continue
        evidence_rows = await c.fetch(
            """SELECT e.*,l.id AS link_id,l.status AS link_status,
          l.association_scope,l.regimen_content_hash,l.hash_contract_version,s.status AS source_status,
          s.verification_state AS source_verification,s.source_version_text
          FROM knowledge.regimen_evidence_link l JOIN knowledge.evidence_record_version e ON e.id=l.evidence_record_version_id
          LEFT JOIN knowledge.source_document_version s ON s.id=e.source_document_version_id
          WHERE l.regimen_version_id=$1 AND e.status<>'RETIRED' AND l.status<>'RETIRED'""",
            row["version_id"],
        )
        evidence = []
        for e in evidence_rows:
            released = (
                e["status"] == e["link_status"] == e["source_status"] == "PUBLISHED"
                and e["source_verification"] == "VERIFIED"
                and bool(e["source_version_text"])
            )
            evidence.append(
                EvidenceReference(
                    ref=Reference(
                        namespace="knowledge.evidence_record_version",
                        id=str(e["id"]),
                        version=str(e["version_no"]),
                        content_hash=e["content_hash"],
                    ),
                    source_code=e["source_code"],
                    scope="REGIMEN"
                    if e["association_scope"] == "REGIMEN_COMBINATION"
                    else "DRUG_ONLY",
                    status="PUBLISHED" if released else "DRAFT",
                    applicable=evidence_link_matches(
                        dict(e), row["document_tree"], row["medication_snapshot"]
                    ),
                    excerpt=e["verbatim_excerpt"],
                    source_locator=e["source_locator"],
                    source_recommendation_raw=e["source_recommendation_raw"],
                    source_evidence_category_raw=e["source_evidence_category_raw"],
                )
            )
        plans.append(
            CandidateInput(
                regimen_id=row["regimen_id"],
                version_id=row["version_id"],
                regimen_code=row["regimen_code"],
                display_name=row["display_name"],
                template_hash=blueprint_hash,
                template_status="PUBLISHED" if row["status"] == "PUBLISHED" else "DRAFT",
                applicability=applicable,
                requirements=requirements,
                evidence=evidence,
                rules=[
                    entry.rule for entry in rules if row["version_id"] in entry.applies_to_versions
                ],
            )
        )
    manifest["applicability"] = [p.applicability.ref.model_dump(mode="json") for p in plans]
    manifest["evidence"] = [e.ref.model_dump(mode="json") for p in plans for e in p.evidence]
    if not plans:
        manifest["configuration_state"] = "APPLICABILITY_NOT_CONFIGURED"
    return plans, manifest


async def fixed_projection(c, hospital_id, detail, principal):
    await c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"{hospital_id}:{detail.version_id}"
    )
    payload = detail.model_dump(mode="json")
    digest = fingerprint(payload)
    row = await c.fetchrow(
        """SELECT * FROM catalog_bridge.template_version_reference
       WHERE hospital_id=$1 AND catalog_namespace='regimen_catalog' AND external_regimen_version_id=$2""",
        hospital_id,
        detail.version_id,
    )
    if row:
        if row["content_hash"] != digest:
            raise BusinessError(
                "FIXED_VERSION_CHANGED", "固定来源版本内容已变化，须核对版本追溯", 409
            )
        return row["id"]
    row = await insert(
        c,
        "catalog_bridge.template_version_reference",
        dict(
            created_by_principal=principal,
            hospital_id=hospital_id,
            catalog_namespace="regimen_catalog",
            external_regimen_id=detail.regimen_id,
            external_regimen_version_id=detail.version_id,
            regimen_code=detail.regimen_code,
            version_label=str(detail.version_no),
            source_status=detail.version_status,
            snapshot_schema_version="template-projection.v1",
            template_payload=payload,
            content_hash=digest,
            availability_state="TEST_ONLY",
        ),
    )
    return row["id"]
