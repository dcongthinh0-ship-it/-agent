"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg


async def count_regimens(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT count(*) FROM regimen_catalog.regimen r\n                WHERE $1 = '' OR strpos(lower(r.regimen_code),lower($1))>0\n                   OR strpos(lower(r.display_name),lower($1))>0\n                   OR strpos(lower(coalesce(r.cancer_category,'')),lower($1))>0",
        *values,
    )


async def list_latest_regimens(c: asyncpg.Connection, *values):
    return await c.fetch(
        "WITH latest AS (\n                   SELECT DISTINCT ON (regimen_id) id,regimen_id,version_no,status\n                   FROM regimen_catalog.regimen_version\n                   ORDER BY regimen_id,version_no DESC,id\n                 ), evidence_counts AS (\n                   SELECT regimen_version_id,count(*) AS evidence_link_count\n                   FROM knowledge.regimen_evidence_link GROUP BY regimen_version_id\n                 )\n                 SELECT r.id AS regimen_id,r.regimen_code,r.display_name,r.cancer_category,\n                        v.id AS version_id,v.version_no,v.status AS version_status,\n                        coalesce(ec.evidence_link_count,0) AS evidence_link_count\n                 FROM regimen_catalog.regimen r\n                 JOIN latest v ON v.regimen_id=r.id\n                 LEFT JOIN evidence_counts ec ON ec.regimen_version_id=v.id\n                 WHERE $1 = '' OR strpos(lower(r.regimen_code),lower($1))>0\n                    OR strpos(lower(r.display_name),lower($1))>0\n                    OR strpos(lower(coalesce(r.cancer_category,'')),lower($1))>0\n                 ORDER BY r.regimen_code LIMIT $2 OFFSET $3",
        *values,
    )


async def get_regimen_regimen(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT r.id AS regimen_id,r.regimen_code,r.display_name,r.cancer_category,\n                          v.id AS version_id,v.version_no,v.status AS version_status,\n                          b.schema_version AS blueprint_schema_version,\n                          b.document_tree::text AS document_tree\n                   FROM regimen_catalog.regimen r\n                   JOIN regimen_catalog.regimen_version v ON v.regimen_id=r.id\n                   LEFT JOIN regimen_catalog.form_blueprint b ON b.regimen_version_id=v.id\n                   WHERE r.id=$1 AND v.id=$2",
        *values,
    )


async def list_medications(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT m.item_key,m.display_order,m.source_drug_name,d.generic_name,\n                          m.standard_dose_text,m.dose_unit,m.dose_basis,m.route_text,\n                          m.frequency_text,m.administration_day_text\n                   FROM regimen_catalog.regimen_medication_item m\n                   LEFT JOIN regimen_catalog.drug_concept d ON d.id=m.drug_concept_id\n                   WHERE m.regimen_version_id=$1 ORDER BY m.display_order,m.item_key LIMIT 250",
        *values,
    )


async def list_fields(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT field_key,label,value_type,widget_type,source_type,edit_policy,\n                          required,repeatable,display_order,default_value::text AS default_value\n                   FROM regimen_catalog.field_definition\n                   WHERE regimen_version_id=$1 OR form_component_id=(\n                     SELECT id FROM regimen_catalog.form_component\n                     WHERE component_code='PHYSICIAN_ROLES' AND status IN ('DRAFT','PUBLISHED')\n                     ORDER BY version_no DESC LIMIT 1\n                   )\n                   ORDER BY (form_component_id IS NOT NULL),display_order,field_key",
        *values,
    )


async def list_content_blocks(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT section_code,block_type,display_order,title,raw_text\n                   FROM regimen_catalog.content_item WHERE regimen_version_id=$1\n                   ORDER BY section_code,display_order,id LIMIT 201",
        *values,
    )


async def has_layout_assets(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT to_regclass('catalog_bridge.form_layout_asset') IS NOT NULL", *values
    )


async def get_verified_layout(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT layout_payload::text,verification_report::text\n                  FROM catalog_bridge.form_layout_asset\n                  WHERE regimen_version_id=$1 AND blueprint_hash=$2\n                  ORDER BY created_at DESC LIMIT 1",
        *values,
    )


async def version_exists(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT 1 FROM regimen_catalog.regimen_version v\n                   WHERE v.id=$1 AND v.regimen_id=$2",
        *values,
    )


async def list_linked_evidence(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT l.id AS association_id,l.association_scope,\n                          l.status AS association_status,e.id AS evidence_id,e.source_code,\n                          e.status AS evidence_status,s.title AS source_title,\n                          s.source_version_text AS source_version,s.status AS source_status,\n                          e.source_recommendation_raw,e.source_evidence_category_raw,\n                          e.internal_level,e.evidence_grade,e.grade_mapping_version,\n                          e.verbatim_excerpt\n                   FROM knowledge.regimen_evidence_link l\n                   JOIN knowledge.evidence_record_version e ON e.id=l.evidence_record_version_id\n                   LEFT JOIN knowledge.source_document_version s\n                     ON s.id=e.source_document_version_id\n                   WHERE l.regimen_version_id=$1\n                   ORDER BY e.source_code,e.evidence_key,l.id LIMIT 101",
        *values,
    )


async def get_evidence(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT e.id AS evidence_id,e.source_code,e.status AS evidence_status,\n                          s.title AS source_title,s.source_version_text AS source_version,\n                          s.status AS source_status,s.source_url,s.source_date::text,\n                          e.source_recommendation_raw,e.source_evidence_category_raw,\n                          e.internal_level,e.evidence_grade,e.grade_mapping_version,\n                          e.verbatim_excerpt,e.source_locator::text AS source_locator,\n                          e.disease_scope::text AS disease_scope,\n                          e.context_description::text AS context_description\n                   FROM knowledge.evidence_record_version e\n                   LEFT JOIN knowledge.source_document_version s\n                     ON s.id=e.source_document_version_id\n                   WHERE e.id=$1",
        *values,
    )
