"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg

from chemo_agent_product.persistence.database import insert


async def reserve_command(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "INSERT INTO ops.command_receipt\n          (created_by_principal,hospital_id,caller_scope,operation_code,request_key,request_hash,status)\n          VALUES($1,$2,$1,$3,$4,$5,'PROCESSING') ON CONFLICT DO NOTHING RETURNING *",
        *values,
    )


async def get_command(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM ops.command_receipt\n          WHERE hospital_id=$1 AND caller_scope=$2 AND operation_code=$3 AND request_key=$4 FOR UPDATE",
        *values,
    )


async def complete_command(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE ops.command_receipt SET status='COMPLETED',response_summary=$2,\n          resource_type=$3,resource_id=$4 WHERE id=$1",
        *values,
    )


async def insert_audit_event(c: asyncpg.Connection, values: dict):
    return await insert(c, "ops.audit_event", values)
