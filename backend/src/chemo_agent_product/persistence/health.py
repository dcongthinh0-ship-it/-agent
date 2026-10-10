"""Read-only startup schema capability check; does not migrate or seed data."""

import asyncio
import logging

from chemo_agent_product.core.observability import emit

logger = logging.getLogger(__name__)


async def workflow_schema_ready(pool):
    return await pool.fetchval("SELECT to_regclass('ops.product_migration') IS NOT NULL")


async def database_available(pool):
    try:
        async with asyncio.timeout(2):
            return await pool.fetchval("SELECT 1") == 1
    except Exception as exc:
        emit(
            logger,
            "database_error",
            level=logging.ERROR,
            exc=exc,
            error_code="CATALOG_UNAVAILABLE",
            component="catalog",
        )
        return False
