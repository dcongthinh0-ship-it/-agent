"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg

from chemo_agent_product.persistence.database import insert


async def get_scoped_context(c: asyncpg.Connection, *values, lock=False):
    return await c.fetchrow(
        "SELECT lc.*,pr.snapshot_id,pr.status AS prepare_status,\n          pr.stage AS prepare_stage,pr.error_code AS prepare_error_code,\n          pat.external_patient_id,enc.external_encounter_id,s.external_staff_id\n          FROM clinical.launch_context lc JOIN clinical.patient_reference pat ON pat.id=lc.patient_reference_id\n          JOIN clinical.encounter_reference enc ON enc.id=lc.encounter_reference_id\n          JOIN clinical.staff_reference s ON s.id=lc.operator_staff_id\n          LEFT JOIN clinical.prepare_run pr ON pr.id=lc.current_prepare_run_id AND pr.generation=lc.active_generation\n          WHERE lc.id=$1 AND lc.hospital_id=$2 AND lc.operator_staff_id=$3"
        + (" FOR UPDATE OF lc" if lock else ""),
        *values,
    )


async def insert_prepare_run(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.prepare_run", values)


async def set_current_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.launch_context SET current_prepare_run_id=$2,active_generation=$3 WHERE id=$1",
        *values,
    )


async def database_time(c: asyncpg.Connection, *values):
    return await c.fetchval("SELECT clock_timestamp()", *values)


async def insert_job(c: asyncpg.Connection, values: dict):
    return await insert(c, "ops.job", values)


async def get_hospital(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM integration.hospital WHERE id=$1", *values)


async def get_staff(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM clinical.staff_reference WHERE id=$1 AND hospital_id=$2", *values
    )


async def lock_session(c: asyncpg.Connection, *values):
    return await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", *values)


async def get_previous_context(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT lc.*,pat.external_patient_id,enc.external_encounter_id\n                FROM clinical.launch_context lc\n                JOIN clinical.patient_reference pat ON pat.id=lc.patient_reference_id\n                JOIN clinical.encounter_reference enc ON enc.id=lc.encounter_reference_id\n                WHERE lc.hospital_id=$1 AND lc.operator_staff_id=$2 AND lc.session_scope_ref=$3\n                ORDER BY lc.host_generation DESC,lc.created_at DESC,lc.id DESC LIMIT 1",
        *values,
    )


async def create_patient_reference(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "INSERT INTO clinical.patient_reference\n              (created_by_principal,hospital_id,external_patient_id,reference_state) VALUES($1,$2,$3,'UNVERIFIED')\n              ON CONFLICT(hospital_id,external_patient_id) DO NOTHING RETURNING *",
        *values,
    )


async def get_patient_reference(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM clinical.patient_reference WHERE hospital_id=$1 AND external_patient_id=$2",
        *values,
    )


async def create_encounter_reference(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "INSERT INTO clinical.encounter_reference\n              (created_by_principal,hospital_id,patient_reference_id,external_encounter_id,last_received_at)\n              VALUES($1,$2,$3,$4,now()) ON CONFLICT(hospital_id,external_encounter_id) DO NOTHING RETURNING *",
        *values,
    )


async def get_encounter_reference(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM clinical.encounter_reference WHERE hospital_id=$1 AND external_encounter_id=$2",
        *values,
    )


async def supersede_session_contexts(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.launch_context SET context_state='SUPERSEDED'\n                WHERE hospital_id=$1 AND operator_staff_id=$2 AND session_scope_ref=$3\n                AND context_state='ACTIVE'",
        *values,
    )


async def insert_launch_context(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.launch_context", values)


async def get_preparation_decision_status(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT d.status,(SELECT count(*) FROM clinical.decision_candidate dc\n                WHERE dc.decision_run_id=d.id) AS candidate_count\n                FROM clinical.decision_run d WHERE d.launch_context_id=$1\n                AND d.prepare_run_id=$2 AND d.purpose='MATCH_CANDIDATES'\n                ORDER BY d.created_at DESC,d.id DESC LIMIT 1",
        *values,
    )


async def get_current_decision(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM clinical.decision_run WHERE launch_context_id=$1\n              AND prepare_run_id=$2 AND purpose='MATCH_CANDIDATES' ORDER BY created_at DESC,id DESC LIMIT 1",
        *values,
    )


async def list_current_candidates(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT cand.*,t.external_regimen_id AS regimen_id,\n              t.external_regimen_version_id AS version_id,t.regimen_code,t.template_payload->>'display_name' AS display_name\n              FROM clinical.decision_candidate cand JOIN catalog_bridge.template_version_reference t ON t.id=cand.template_ref_id\n              WHERE cand.decision_run_id=$1 ORDER BY CASE presentation_region WHEN 'RECOMMENDATION' THEN 0 ELSE 1 END,\n              evidence_level NULLS LAST,t.regimen_code,cand.id",
        *values,
    )


async def get_snapshot(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM clinical.clinical_snapshot WHERE id=$1", *values)


async def list_encounter_instances(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT i.id,i.row_version,i.current_revision_id,i.current_confirmed_revision_id,\n                t.template_payload->>'display_name' AS display_name,\n                (i.origin_context_id<>$4 OR d.snapshot_id IS DISTINCT FROM $5) AS read_only\n                FROM clinical.patient_regimen_instance i\n                JOIN catalog_bridge.template_version_reference t ON t.id=i.template_ref_id\n                LEFT JOIN clinical.decision_candidate dc ON dc.id=i.origin_candidate_id\n                LEFT JOIN clinical.decision_run d ON d.id=dc.decision_run_id\n                WHERE i.hospital_id=$1 AND i.patient_reference_id=$2 AND i.encounter_reference_id=$3\n                AND (i.current_revision_id IS NOT NULL OR i.origin_context_id=$4)\n                ORDER BY i.created_at DESC,i.id DESC",
        *values,
    )


async def supersede_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET status='SUPERSEDED' WHERE id=$1 AND status IN ('QUEUED','RUNNING')",
        *values,
    )
