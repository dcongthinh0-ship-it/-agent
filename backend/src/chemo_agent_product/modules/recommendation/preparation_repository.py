"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg

from chemo_agent_product.persistence.database import insert


async def get_locked_preparation(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT pr.*,lc.hospital_id,lc.patient_reference_id,lc.encounter_reference_id,\n              lc.context_state,lc.expires_at,lc.active_generation,lc.operator_staff_id,pat.external_patient_id,enc.external_encounter_id,\n              staff.external_staff_id FROM clinical.prepare_run pr\n              JOIN clinical.launch_context lc ON lc.id=pr.launch_context_id\n              JOIN clinical.patient_reference pat ON pat.id=lc.patient_reference_id\n              JOIN clinical.encounter_reference enc ON enc.id=lc.encounter_reference_id\n              JOIN clinical.staff_reference staff ON staff.id=lc.operator_staff_id WHERE pr.id=$1 FOR UPDATE OF pr,lc",
        *values,
    )


async def supersede_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET status='SUPERSEDED',completed_at=now() WHERE id=$1",
        *values,
    )


async def cancel_job(c: asyncpg.Connection, *values):
    return await c.execute("UPDATE ops.job SET status='CANCELLED' WHERE id=$1", *values)


async def start_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET status='RUNNING',stage='FETCHING',started_at=coalesce(started_at,now()),error_code=NULL WHERE id=$1",
        *values,
    )


async def insert_hospital_call_attempt(c: asyncpg.Connection, values: dict):
    return await insert(c, "integration.hospital_call_attempt", values)


async def complete_hospital_attempt(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE integration.hospital_call_attempt\n                    SET completed_at=$4,transport_outcome=$5,http_status=$6,business_code=$7,\n                    response_hash=$8,safe_error_summary=$9\n                    WHERE request_id=$1 AND prepare_run_id=$2 AND hospital_id=$3",
        *values,
    )


async def get_locked_context(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM clinical.launch_context WHERE id=$1 FOR UPDATE", *values)


async def lock_snapshot(c: asyncpg.Connection, *values):
    return await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", *values)


async def create_source_record(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "INSERT INTO clinical.source_record\n                  (created_by_principal,hospital_id,patient_reference_id,source_operation,source_system,source_record_key,\n                   received_at,identity_verification,record_payload,payload_schema_version,content_hash,source_encounter_external_id,request_attempt_id)\n                  VALUES($1,$2,$3,$4,'HOSPITAL_ADAPTER',$5,$6,$7,$8,'hospital-response.v1',$9,$10,$11)\n                  ON CONFLICT(hospital_id,patient_reference_id,source_operation,source_record_key,content_hash)\n                  DO NOTHING RETURNING id",
        *values,
    )


async def find_source_record(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT id FROM clinical.source_record WHERE hospital_id=$1 AND patient_reference_id=$2\n                  AND source_operation=$3 AND source_record_key=$4 AND content_hash=$5",
        *values,
    )


async def next_snapshot_number(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT coalesce(max(snapshot_no),0)+1 FROM clinical.clinical_snapshot WHERE encounter_reference_id=$1",
        *values,
    )


async def insert_clinical_snapshot(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.clinical_snapshot", values)


async def insert_snapshot_source(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.snapshot_source", values)


async def start_matching(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET snapshot_id=$2,stage='MATCHING' WHERE id=$1", *values
    )


async def insert_decision_run(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.decision_run", values)


async def insert_decision_candidate(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.decision_candidate", values)


async def complete_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET status='SUCCEEDED',stage='READY',completed_at=now(),source_completion=$2 WHERE id=$1",
        *values,
    )


async def complete_job(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE ops.job SET status='SUCCEEDED',lease_expires_at=NULL WHERE id=$1", *values
    )
