from __future__ import annotations

from uuid import UUID

from pydantic import Field

from chemo_agent_product.core.domain import (
    Applicability,
    CandidateInput,
    ClinicalRule,
    Contract,
    EvidenceReference,
    FactRequirement,
    Reference,
    fingerprint,
)
from chemo_agent_product.core.security import BusinessError

from . import repository


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
    rows = await repository.load_inputs_regimen_medication_item(c, disease)
    packages = await repository.list_published_rules(c)
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
        relations = await repository.list_published_applicabilities(c, row["version_id"], disease)
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
            # The exact catalog category is an unverified test fallback.
            # It does not infer synonyms, pathology or off-label eligibility.
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
        evidence_rows = await repository.load_inputs_regimen_evidence_link(c, row["version_id"])
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
    await repository.lock_template_projection(c, f"{hospital_id}:{detail.version_id}")
    payload = detail.model_dump(mode="json")
    digest = fingerprint(payload)
    row = await repository.get_template_projection(c, hospital_id, detail.version_id)
    if row:
        if row["content_hash"] != digest:
            raise BusinessError(
                "FIXED_VERSION_CHANGED", "固定来源版本内容已变化，须核对版本追溯", 409
            )
        return row["id"]
    row = await repository.insert_template_version_reference(
        c,
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
