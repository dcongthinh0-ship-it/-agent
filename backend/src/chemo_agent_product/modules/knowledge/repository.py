"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg

from chemo_agent_product.persistence.database import insert


async def load_inputs_regimen_medication_item(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT r.id AS regimen_id,r.regimen_code,r.display_name,r.cancer_category,\n      v.id AS version_id,v.status,b.document_tree,\n      (SELECT coalesce(jsonb_agg(jsonb_build_object(\n        'name',m.source_drug_name,'dose',m.standard_dose_text,\n        'day',m.administration_day_text,'drug_concept_id',m.drug_concept_id)\n        ORDER BY m.display_order),'[]'::jsonb)\n        FROM regimen_catalog.regimen_medication_item m WHERE m.regimen_version_id=v.id\n      ) AS medication_snapshot FROM regimen_catalog.regimen r\n      JOIN regimen_catalog.regimen_version v ON v.regimen_id=r.id\n      JOIN regimen_catalog.form_blueprint b ON b.regimen_version_id=v.id\n      WHERE r.active AND v.status<>'RETIRED' AND (r.cancer_category=$1 OR EXISTS(\n        SELECT 1 FROM knowledge.regimen_applicability_version a WHERE a.regimen_version_id=v.id\n        AND a.disease_code=$1 AND a.status<>'RETIRED'))",
        *values,
    )


async def list_published_rules(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT DISTINCT ON(package_key) * FROM knowledge.rule_package_version\n      WHERE status='PUBLISHED' ORDER BY package_key,version_no DESC",
        *values,
    )


async def list_published_applicabilities(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT DISTINCT ON(applicability_key) *\n          FROM knowledge.regimen_applicability_version WHERE regimen_version_id=$1 AND disease_code=$2\n          AND status<>'RETIRED' ORDER BY applicability_key,version_no DESC",
        *values,
    )


async def load_inputs_regimen_evidence_link(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT e.*,l.id AS link_id,l.status AS link_status,\n          l.association_scope,l.regimen_content_hash,l.hash_contract_version,s.status AS source_status,\n          s.verification_state AS source_verification,s.source_version_text\n          FROM knowledge.regimen_evidence_link l JOIN knowledge.evidence_record_version e ON e.id=l.evidence_record_version_id\n          LEFT JOIN knowledge.source_document_version s ON s.id=e.source_document_version_id\n          WHERE l.regimen_version_id=$1 AND e.status<>'RETIRED' AND l.status<>'RETIRED'",
        *values,
    )


async def lock_template_projection(c: asyncpg.Connection, *values):
    return await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", *values)


async def get_template_projection(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM catalog_bridge.template_version_reference\n       WHERE hospital_id=$1 AND catalog_namespace='regimen_catalog' AND external_regimen_version_id=$2",
        *values,
    )


async def insert_template_version_reference(c: asyncpg.Connection, values: dict):
    return await insert(c, "catalog_bridge.template_version_reference", values)
