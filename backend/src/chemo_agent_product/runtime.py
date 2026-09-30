from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

import asyncpg

from chemo_agent_product.contracts import ContextReadout, PreparedCandidate


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
            row = await connection.fetchrow(
                """SELECT lc.id AS context_id,lc.hospital_id,lc.context_state,lc.expires_at,
                          p.external_patient_id AS patient_ref,
                          e.external_encounter_id AS encounter_ref,
                          pr.id AS prepare_run_id,pr.status AS prepare_status,
                          pr.stage AS prepare_stage,pr.error_code AS prepare_error_code,
                          dr.id AS decision_run_id,dr.status AS decision_status,
                          dr.outcome_code
                   FROM clinical.launch_context lc
                   JOIN integration.hospital h ON h.id=lc.hospital_id
                     AND h.status='TEST_ONLY' AND left(h.hospital_key,5)='TEST_'
                   JOIN clinical.patient_reference p ON p.id=lc.patient_reference_id
                     AND p.hospital_id=lc.hospital_id
                     AND left(p.external_patient_id,6)='SYNTH_'
                   JOIN clinical.encounter_reference e ON e.id=lc.encounter_reference_id
                     AND e.patient_reference_id=p.id AND e.hospital_id=lc.hospital_id
                     AND left(e.external_encounter_id,6)='SYNTH_'
                   LEFT JOIN clinical.prepare_run pr ON pr.id=lc.current_prepare_run_id
                     AND pr.launch_context_id=lc.id AND pr.generation=lc.active_generation
                   LEFT JOIN LATERAL (
                     SELECT d.id,d.status,d.outcome_code
                     FROM clinical.decision_run d
                     WHERE d.launch_context_id=lc.id AND d.prepare_run_id=pr.id
                       AND d.snapshot_id=pr.snapshot_id AND d.usage_mode='TEST_ONLY'
                       AND d.purpose='MATCH_CANDIDATES'
                     ORDER BY d.created_at DESC,d.id DESC LIMIT 1
                   ) dr ON true
                   WHERE lc.id=$1""",
                context_id,
            )
            if row is None:
                return None
            candidates = []
            if row["decision_run_id"] is not None and row["decision_status"] == "SUCCEEDED":
                candidates = await connection.fetch(
                    """SELECT c.id AS candidate_id,t.external_regimen_id AS regimen_id,
                              t.regimen_code,t.template_payload->>'display_name' AS display_name,
                              t.external_regimen_version_id AS version_id,
                              t.source_status AS version_status,c.presentation_region,
                              c.rank_group,c.evidence_state,c.evidence_level,c.evidence_grade,
                              c.data_labels::text AS data_labels,
                              c.safety_labels::text AS safety_labels,c.x_reason_code
                       FROM clinical.decision_candidate c
                       LEFT JOIN catalog_bridge.template_version_reference t
                         ON t.id=c.template_ref_id AND t.hospital_id=$2
                           AND t.availability_state='TEST_ONLY'
                       WHERE c.decision_run_id=$1
                         AND c.presentation_region IN ('RECOMMENDATION','X_EXCLUDED')
                       ORDER BY CASE c.presentation_region WHEN 'RECOMMENDATION' THEN 0 ELSE 1 END,
                                c.rank_group NULLS LAST,c.candidate_key""",
                    row["decision_run_id"],
                    row["hospital_id"],
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
