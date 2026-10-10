"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg

from chemo_agent_product.persistence.database import insert


async def get_prepared_decision(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM clinical.decision_run WHERE launch_context_id=$1\n          AND prepare_run_id=$2 AND purpose='MATCH_CANDIDATES' AND status='SUCCEEDED'\n          ORDER BY created_at DESC LIMIT 1",
        *values,
    )


async def get_scoped_review_revision(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT r.*,i.template_ref_id FROM clinical.patient_regimen_revision r\n              JOIN clinical.patient_regimen_instance i ON i.id=r.instance_id WHERE r.id=$1 AND i.origin_context_id=$2",
        *values,
    )


async def insert_decision_run(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.decision_run", values)


async def lock_profile(c: asyncpg.Connection, *values):
    return await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", *values)


async def find_profile(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM agent.agent_profile_version WHERE config_hash=$1 AND profile_kind=$2",
        *values,
    )


async def next_profile_version(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT coalesce(max(version_no),0)+1 FROM agent.agent_profile_version WHERE profile_key=$1",
        *values,
    )


async def insert_agent_profile_version(c: asyncpg.Connection, values: dict):
    return await insert(c, "agent.agent_profile_version", values)


async def get_snapshot(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM clinical.clinical_snapshot WHERE id=$1", *values)


async def find_snapshot_decision(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT id FROM clinical.decision_run WHERE launch_context_id=$1\n          AND purpose='MATCH_CANDIDATES' AND snapshot_id=$2 ORDER BY created_at DESC LIMIT 1",
        *values,
    )


async def insert_agent_run(c: asyncpg.Connection, values: dict):
    return await insert(c, "agent.agent_run", values)


async def database_time(c: asyncpg.Connection, *values):
    return await c.fetchval("SELECT clock_timestamp()", *values)


async def insert_job(c: asyncpg.Connection, values: dict):
    return await insert(c, "ops.job", values)


async def get_scoped_run(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT a.*,p.profile_kind FROM agent.agent_run a\n              JOIN agent.agent_profile_version p ON p.id=a.profile_version_id JOIN clinical.decision_run d ON d.id=a.decision_run_id\n              WHERE a.id=$1 AND d.launch_context_id=$2",
        *values,
    )


async def list_outputs(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT output_kind,validation_state,structured_payload FROM agent.agent_output WHERE agent_run_id=$1",
        *values,
    )


async def list_tool_calls(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT call_no,tool_name,status,error_code,started_at,completed_at FROM agent.agent_tool_call WHERE agent_run_id=$1 ORDER BY call_no",
        *values,
    )


async def list_scoped_runs(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT a.id,a.status,a.error_code,a.created_at\n              FROM agent.agent_run a JOIN agent.agent_profile_version ap ON ap.id=a.profile_version_id\n              JOIN clinical.decision_run d ON d.id=a.decision_run_id\n              WHERE d.launch_context_id=$1 AND ap.profile_kind=$2\n              AND (($3::uuid IS NULL AND d.snapshot_id=$4) OR d.target_revision_id=$3)\n              ORDER BY a.created_at DESC,a.id DESC LIMIT 10",
        *values,
    )


async def get_locked_run(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT a.*,p.profile_kind,p.config_hash FROM agent.agent_run a\n              JOIN agent.agent_profile_version p ON p.id=a.profile_version_id WHERE a.id=$1 FOR UPDATE OF a",
        *values,
    )


async def start_run(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_run SET status='RUNNING',started_at=now() WHERE id=$1", *values
    )


async def insert_agent_output(c: asyncpg.Connection, values: dict):
    return await insert(c, "agent.agent_output", values)


async def complete_run(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_run SET status='SUCCEEDED',completed_at=now(),usage_summary=$2 WHERE id=$1",
        *values,
    )


async def complete_job(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE ops.job SET status='SUCCEEDED',lease_expires_at=NULL WHERE id=$1", *values
    )


async def fail_run(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_run SET status=$2,error_code=$3,completed_at=now() WHERE id=$1", *values
    )


async def fail_job(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE ops.job SET status='FAILED',last_error_code=$2,lease_expires_at=NULL WHERE id=$1",
        *values,
    )


async def get_run_context(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT lc.context_state,lc.expires_at,lc.current_prepare_run_id,d.prepare_run_id\n          FROM clinical.decision_run d JOIN clinical.launch_context lc ON lc.id=d.launch_context_id WHERE d.id=$1",
        *values,
    )


async def get_snapshot_payload(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1", *values
    )


async def list_bound_candidates(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT dc.*,t.template_payload,t.content_hash AS template_hash,t.id AS projection_id\n              FROM clinical.decision_candidate dc JOIN catalog_bridge.template_version_reference t ON t.id=dc.template_ref_id\n              WHERE dc.decision_run_id=$1 ORDER BY presentation_region,evidence_level NULLS LAST,candidate_key",
        *values,
    )


async def get_fixed_evidence(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM knowledge.evidence_record_version WHERE id=$1", *values)


async def get_revision(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM clinical.patient_regimen_revision WHERE id=$1", *values)


async def list_revision_orders(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT * FROM clinical.patient_regimen_order_item WHERE revision_id=$1 ORDER BY line_no",
        *values,
    )


async def list_revision_fields(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT field_key,value_json,value_state,value_source,provenance_ref FROM clinical.patient_regimen_field_value WHERE revision_id=$1 ORDER BY field_key",
        *values,
    )


async def lock_run(c: asyncpg.Connection, *values):
    return await c.execute("SELECT id FROM agent.agent_run WHERE id=$1 FOR UPDATE", *values)


async def next_call_number(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT coalesce(max(call_no),0)+1 FROM agent.agent_tool_call WHERE agent_run_id=$1",
        *values,
    )


async def insert_agent_tool_call(c: asyncpg.Connection, values: dict):
    return await insert(c, "agent.agent_tool_call", values)


async def complete_tool_call(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_tool_call SET status='SUCCEEDED',result_reference=$2,result_hash=$3,completed_at=now() WHERE id=$1",
        *values,
    )


async def fail_tool_call(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_tool_call SET status=$3,error_code=$2,completed_at=now() WHERE id=$1",
        *values,
    )
