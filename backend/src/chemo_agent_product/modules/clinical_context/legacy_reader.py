from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

import asyncpg

from chemo_agent_product.modules.clinical_context.schemas import ContextReadout
from chemo_agent_product.modules.recommendation.schemas import PreparedCandidate

from . import legacy_repository as repository


class ContextReader(Protocol):
    async def get_context(self, context_id: UUID) -> ContextReadout | None: ...


def decode_json_array(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    return []


class PostgresTestContextReader:
    """Read only synthetic TEST_ONLY runs from an explicitly separate test database."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_context(self, context_id: UUID) -> ContextReadout | None:
        async with self.pool.acquire() as connection, connection.transaction(readonly=True):
            row = await repository.get_context(connection, context_id)
            if row is None:
                return None
            candidates = []
            if row["decision_run_id"] is not None and row["decision_status"] == "SUCCEEDED":
                candidates = await repository.list_candidates(
                    connection, row["decision_run_id"], row["hospital_id"]
                )
        items = []
        for candidate in candidates:
            value = dict(candidate)
            value["data_labels"] = decode_json_array(value["data_labels"])
            value["safety_labels"] = decode_json_array(value["safety_labels"])
            items.append(PreparedCandidate.model_validate(value))
        payload = dict(row)
        payload["expires_at"] = row["expires_at"].isoformat()
        payload["candidates"] = items
        return ContextReadout.model_validate(payload)
