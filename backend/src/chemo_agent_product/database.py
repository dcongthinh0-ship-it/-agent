from __future__ import annotations

import json
from typing import Any

import asyncpg


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


async def initialize(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec("json", schema="pg_catalog", encoder=encode, decoder=json.loads)
    await connection.set_type_codec(
        "jsonb", schema="pg_catalog", encoder=encode, decoder=json.loads
    )


async def open_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn, min_size=1, max_size=8, command_timeout=20, init=initialize
    )


async def insert(
    connection: asyncpg.Connection, table: str, values: dict[str, Any]
) -> asyncpg.Record:
    # Table/column names only come from program constants, never Agent or request strings.
    allowed = {"clinical", "integration", "catalog_bridge", "ops", "agent", "knowledge"}
    schema, name = table.split(".")
    if schema not in allowed or not name.replace("_", "").isalnum():
        raise ValueError("invalid internal table")
    columns = list(values)
    if not all(c.replace("_", "").isalnum() for c in columns):
        raise ValueError("invalid internal column")
    placeholders = ",".join(f"${i + 1}" for i in range(len(columns)))
    return await connection.fetchrow(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) RETURNING *",
        *values.values(),
    )
