"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg


async def get_context(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT lc.id AS context_id,lc.hospital_id,lc.context_state,lc.expires_at,\n                          p.external_patient_id AS patient_ref,\n                          e.external_encounter_id AS encounter_ref,\n                          pr.id AS prepare_run_id,pr.status AS prepare_status,\n                          pr.stage AS prepare_stage,pr.error_code AS prepare_error_code,\n                          dr.id AS decision_run_id,dr.status AS decision_status,\n                          dr.outcome_code\n                   FROM clinical.launch_context lc\n                   JOIN integration.hospital h ON h.id=lc.hospital_id\n                     AND h.status='TEST_ONLY' AND left(h.hospital_key,5)='TEST_'\n                   JOIN clinical.patient_reference p ON p.id=lc.patient_reference_id\n                     AND p.hospital_id=lc.hospital_id\n                     AND left(p.external_patient_id,6)='SYNTH_'\n                   JOIN clinical.encounter_reference e ON e.id=lc.encounter_reference_id\n                     AND e.patient_reference_id=p.id AND e.hospital_id=lc.hospital_id\n                     AND left(e.external_encounter_id,6)='SYNTH_'\n                   LEFT JOIN clinical.prepare_run pr ON pr.id=lc.current_prepare_run_id\n                     AND pr.launch_context_id=lc.id AND pr.generation=lc.active_generation\n                   LEFT JOIN LATERAL (\n                     SELECT d.id,d.status,d.outcome_code\n                     FROM clinical.decision_run d\n                     WHERE d.launch_context_id=lc.id AND d.prepare_run_id=pr.id\n                       AND d.snapshot_id=pr.snapshot_id AND d.usage_mode='TEST_ONLY'\n                       AND d.purpose='MATCH_CANDIDATES'\n                     ORDER BY d.created_at DESC,d.id DESC LIMIT 1\n                   ) dr ON true\n                   WHERE lc.id=$1",
        *values,
    )


async def list_candidates(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT c.id AS candidate_id,t.external_regimen_id AS regimen_id,\n                              t.regimen_code,t.template_payload->>'display_name' AS display_name,\n                              t.external_regimen_version_id AS version_id,\n                              t.source_status AS version_status,c.presentation_region,\n                              c.rank_group,c.evidence_state,c.evidence_level,c.evidence_grade,\n                              c.data_labels::text AS data_labels,\n                              c.safety_labels::text AS safety_labels,c.x_reason_code\n                       FROM clinical.decision_candidate c\n                       LEFT JOIN catalog_bridge.template_version_reference t\n                         ON t.id=c.template_ref_id AND t.hospital_id=$2\n                           AND t.availability_state='TEST_ONLY'\n                       WHERE c.decision_run_id=$1\n                         AND c.presentation_region IN ('RECOMMENDATION','X_EXCLUDED')\n                       ORDER BY CASE c.presentation_region WHEN 'RECOMMENDATION' THEN 0 ELSE 1 END,\n                                c.rank_group NULLS LAST,c.candidate_key",
        *values,
    )
