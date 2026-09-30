from __future__ import annotations

import os

import asyncpg
import pytest

from chemo_agent_product.catalog import PostgresCatalogReader


@pytest.mark.asyncio
async def test_current_catalog_links_fixed_version_to_evidence() -> None:
    dsn = os.getenv("CHEMO_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("需要显式配置只读数据库测试连接")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        reader = PostgresCatalogReader(pool)
        page = await reader.list_regimens("WFAH-DIG-017", 1, 10)
        assert len(page.items) == 1
        item = page.items[0]
        assert item.version_status == "DRAFT"
        detail = await reader.get_regimen(item.regimen_id, item.version_id)
        assert detail is not None
        assert detail.version_id == item.version_id
        assert detail.medications
        evidence = await reader.list_evidence(item.regimen_id, item.version_id)
        assert evidence is not None
        assert evidence.items
        assert all(row.association_status == "DRAFT" for row in evidence.items)
        record = await reader.get_evidence(evidence.items[0].evidence_id)
        assert record is not None
        assert record.evidence_status == "DRAFT"
        assert record.verbatim_excerpt
    finally:
        await pool.close()
