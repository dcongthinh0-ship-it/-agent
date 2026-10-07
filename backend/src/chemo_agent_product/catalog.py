from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

import asyncpg

from chemo_agent_product.contracts import (
    ContentBlock,
    EvidenceDetail,
    EvidenceList,
    EvidenceSummary,
    FieldDefinition,
    MedicationItem,
    RegimenDetail,
    RegimenPage,
    RegimenSummary,
)


class CatalogReader(Protocol):
    async def list_regimens(self, search: str, page: int, page_size: int) -> RegimenPage: ...

    async def get_regimen(self, regimen_id: UUID, version_id: UUID) -> RegimenDetail | None: ...

    async def list_evidence(self, regimen_id: UUID, version_id: UUID) -> EvidenceList | None: ...

    async def get_evidence(self, evidence_id: UUID) -> EvidenceDetail | None: ...


def display_source(source_code: str) -> str:
    return "FDA" if source_code.upper() in {"FDA", "DAILYMED"} else source_code


class PostgresCatalogReader:
    """只读固定版本，不读取患者运行表，也不产生发布状态。"""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def list_regimens(self, search: str, page: int, page_size: int) -> RegimenPage:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            total = await connection.fetchval(
                """SELECT count(*) FROM regimen_catalog.regimen r
                WHERE $1 = '' OR strpos(lower(r.regimen_code),lower($1))>0
                   OR strpos(lower(r.display_name),lower($1))>0
                   OR strpos(lower(coalesce(r.cancer_category,'')),lower($1))>0""",
                search,
            )
            rows = await connection.fetch(
                """WITH latest AS (
                   SELECT DISTINCT ON (regimen_id) id,regimen_id,version_no,status
                   FROM regimen_catalog.regimen_version
                   ORDER BY regimen_id,version_no DESC,id
                 ), evidence_counts AS (
                   SELECT regimen_version_id,count(*) AS evidence_link_count
                   FROM knowledge.regimen_evidence_link GROUP BY regimen_version_id
                 )
                 SELECT r.id AS regimen_id,r.regimen_code,r.display_name,r.cancer_category,
                        v.id AS version_id,v.version_no,v.status AS version_status,
                        coalesce(ec.evidence_link_count,0) AS evidence_link_count
                 FROM regimen_catalog.regimen r
                 JOIN latest v ON v.regimen_id=r.id
                 LEFT JOIN evidence_counts ec ON ec.regimen_version_id=v.id
                 WHERE $1 = '' OR strpos(lower(r.regimen_code),lower($1))>0
                    OR strpos(lower(r.display_name),lower($1))>0
                    OR strpos(lower(coalesce(r.cancer_category,'')),lower($1))>0
                 ORDER BY r.regimen_code LIMIT $2 OFFSET $3""",
                search,
                page_size,
                (page - 1) * page_size,
            )
        return RegimenPage(
            items=[RegimenSummary.model_validate(dict(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_regimen(self, regimen_id: UUID, version_id: UUID) -> RegimenDetail | None:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            row = await connection.fetchrow(
                """SELECT r.id AS regimen_id,r.regimen_code,r.display_name,r.cancer_category,
                          v.id AS version_id,v.version_no,v.status AS version_status,
                          b.schema_version AS blueprint_schema_version,
                          b.document_tree::text AS document_tree
                   FROM regimen_catalog.regimen r
                   JOIN regimen_catalog.regimen_version v ON v.regimen_id=r.id
                   LEFT JOIN regimen_catalog.form_blueprint b ON b.regimen_version_id=v.id
                   WHERE r.id=$1 AND v.id=$2""",
                regimen_id,
                version_id,
            )
            if row is None:
                return None
            medications = await connection.fetch(
                """SELECT m.item_key,m.display_order,m.source_drug_name,d.generic_name,
                          m.standard_dose_text,m.dose_unit,m.dose_basis,m.route_text,
                          m.frequency_text,m.administration_day_text
                   FROM regimen_catalog.regimen_medication_item m
                   LEFT JOIN regimen_catalog.drug_concept d ON d.id=m.drug_concept_id
                   WHERE m.regimen_version_id=$1 ORDER BY m.display_order,m.item_key LIMIT 250""",
                version_id,
            )
            fields = await connection.fetch(
                """SELECT field_key,label,value_type,widget_type,source_type,edit_policy,
                          required,repeatable,display_order,default_value::text AS default_value
                   FROM regimen_catalog.field_definition
                   WHERE regimen_version_id=$1 OR form_component_id=(
                     SELECT id FROM regimen_catalog.form_component
                     WHERE component_code='PHYSICIAN_ROLES' AND status IN ('DRAFT','PUBLISHED')
                     ORDER BY version_no DESC LIMIT 1
                   )
                   ORDER BY (form_component_id IS NOT NULL),display_order,field_key""",
                version_id,
            )
            field_items = []
            for field in fields:
                value = dict(field)
                value["default_value"] = (
                    json.loads(value["default_value"]) if value["default_value"] else None
                )
                field_items.append(FieldDefinition.model_validate(value))
            blocks = await connection.fetch(
                """SELECT section_code,block_type,display_order,title,raw_text
                   FROM regimen_catalog.content_item WHERE regimen_version_id=$1
                   ORDER BY section_code,display_order,id LIMIT 201""",
                version_id,
            )
        return RegimenDetail.model_validate(
            {
                **dict(row),
                "document_tree": json.loads(row["document_tree"]) if row["document_tree"] else {},
                "fields": field_items,
                "medications": [MedicationItem.model_validate(dict(x)) for x in medications],
                "content_blocks": [ContentBlock.model_validate(dict(x)) for x in blocks[:200]],
                "content_truncated": len(blocks) > 200,
            }
        )

    async def list_evidence(self, regimen_id: UUID, version_id: UUID) -> EvidenceList | None:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            exists = await connection.fetchval(
                """SELECT 1 FROM regimen_catalog.regimen_version v
                   WHERE v.id=$1 AND v.regimen_id=$2""",
                version_id,
                regimen_id,
            )
            if exists is None:
                return None
            rows = await connection.fetch(
                """SELECT l.id AS association_id,l.association_scope,
                          l.status AS association_status,e.id AS evidence_id,e.source_code,
                          e.status AS evidence_status,s.title AS source_title,
                          s.source_version_text AS source_version,s.status AS source_status,
                          e.source_recommendation_raw,e.source_evidence_category_raw,
                          e.internal_level,e.evidence_grade,e.grade_mapping_version,
                          e.verbatim_excerpt
                   FROM knowledge.regimen_evidence_link l
                   JOIN knowledge.evidence_record_version e ON e.id=l.evidence_record_version_id
                   LEFT JOIN knowledge.source_document_version s
                     ON s.id=e.source_document_version_id
                   WHERE l.regimen_version_id=$1
                   ORDER BY e.source_code,e.evidence_key,l.id LIMIT 101""",
                version_id,
            )
        items = []
        for row in rows[:100]:
            item = dict(row)
            item["display_source"] = display_source(item["source_code"])
            excerpt = item.pop("verbatim_excerpt")
            item["excerpt_preview"] = excerpt[:180] if excerpt else None
            items.append(EvidenceSummary.model_validate(item))
        return EvidenceList(regimen_version_id=version_id, items=items, truncated=len(rows) > 100)

    async def get_evidence(self, evidence_id: UUID) -> EvidenceDetail | None:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            row = await connection.fetchrow(
                """SELECT e.id AS evidence_id,e.source_code,e.status AS evidence_status,
                          s.title AS source_title,s.source_version_text AS source_version,
                          s.status AS source_status,s.source_url,s.source_date::text,
                          e.source_recommendation_raw,e.source_evidence_category_raw,
                          e.internal_level,e.evidence_grade,e.grade_mapping_version,
                          e.verbatim_excerpt,e.source_locator::text AS source_locator,
                          e.disease_scope::text AS disease_scope,
                          e.context_description::text AS context_description
                   FROM knowledge.evidence_record_version e
                   LEFT JOIN knowledge.source_document_version s
                     ON s.id=e.source_document_version_id
                   WHERE e.id=$1""",
                evidence_id,
            )
        if row is None:
            return None
        item = dict(row)
        item["display_source"] = display_source(item["source_code"])
        for key in ("source_locator", "disease_scope", "context_description"):
            item[key] = json.loads(item[key]) if item[key] else {}
        locator_keys = {
            "sheet",
            "row_no",
            "pdf_page",
            "fragment_no",
            "source_heading_raw",
            "source_disease_text",
        }
        item["source_locator"] = {
            key: value
            for key, value in item["source_locator"].items()
            if key in locator_keys and value is not None
        }
        return EvidenceDetail.model_validate(item)
