"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg


async def get_revision(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM clinical.patient_regimen_revision WHERE id=$1", *values)


async def list_orders(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT * FROM clinical.patient_regimen_order_item WHERE revision_id=$1 ORDER BY line_no",
        *values,
    )
