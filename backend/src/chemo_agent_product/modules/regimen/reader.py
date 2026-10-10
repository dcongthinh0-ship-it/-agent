from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

import asyncpg

from chemo_agent_product.core.domain import fingerprint
from chemo_agent_product.modules.knowledge.schemas import (
    EvidenceDetail,
    EvidenceList,
    EvidenceSummary,
)
from chemo_agent_product.modules.regimen.schemas import (
    ContentBlock,
    FieldDefinition,
    MedicationItem,
    RegimenDetail,
    RegimenPage,
    RegimenSummary,
)

from . import repository


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
            total = await repository.count_regimens(connection, search)
            rows = await repository.list_latest_regimens(
                connection, search, page_size, (page - 1) * page_size
            )
        return RegimenPage(
            items=[RegimenSummary.model_validate(dict(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_regimen(self, regimen_id: UUID, version_id: UUID) -> RegimenDetail | None:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            row = await repository.get_regimen_regimen(connection, regimen_id, version_id)
            if row is None:
                return None
            medications = await repository.list_medications(connection, version_id)
            fields = await repository.list_fields(connection, version_id)
            field_items = []
            for field in fields:
                value = dict(field)
                value["default_value"] = (
                    json.loads(value["default_value"]) if value["default_value"] else None
                )
                field_items.append(FieldDefinition.model_validate(value))
            blocks = await repository.list_content_blocks(connection, version_id)
            layout = None
            if await repository.has_layout_assets(connection):
                tree = json.loads(row["document_tree"]) if row["document_tree"] else {}
                layout = await repository.get_verified_layout(
                    connection, version_id, fingerprint(tree)
                )
        return RegimenDetail.model_validate(
            {
                **dict(row),
                "document_tree": json.loads(row["document_tree"]) if row["document_tree"] else {},
                "fields": field_items,
                "medications": [MedicationItem.model_validate(dict(x)) for x in medications],
                "content_blocks": [ContentBlock.model_validate(dict(x)) for x in blocks[:200]],
                "content_truncated": len(blocks) > 200,
                "word_layout": json.loads(layout["layout_payload"]) if layout else None,
                "layout_verification": json.loads(layout["verification_report"])
                if layout
                else None,
            }
        )

    async def list_evidence(self, regimen_id: UUID, version_id: UUID) -> EvidenceList | None:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            exists = await repository.version_exists(connection, version_id, regimen_id)
            if exists is None:
                return None
            rows = await repository.list_linked_evidence(connection, version_id)
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
            row = await repository.get_evidence(connection, evidence_id)
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
