"""Read-only startup schema capability check; does not migrate or seed data."""


async def workflow_schema_ready(pool):
    return await pool.fetchval("SELECT to_regclass('ops.product_migration') IS NOT NULL")
