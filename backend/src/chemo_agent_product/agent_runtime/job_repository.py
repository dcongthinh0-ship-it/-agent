"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg


async def expire_exhausted_jobs(c: asyncpg.Connection, *values):
    return await c.fetch(
        "UPDATE ops.job SET status='DEAD_LETTER',last_error_code='LEASE_RECOVERY_EXHAUSTED'\n              WHERE status='RUNNING' AND lease_expires_at<clock_timestamp() AND attempt_count>=max_attempts RETURNING id,prepare_run_id,agent_run_id",
        *values,
    )


async def fail_exhausted_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET status='FAILED',error_code='LEASE_RECOVERY_EXHAUSTED',completed_at=now()\n                       WHERE id=$1 AND status IN ('QUEUED','RUNNING')",
        *values,
    )


async def fail_exhausted_agent(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_run SET status='FAILED',error_code='LEASE_RECOVERY_EXHAUSTED',completed_at=now() WHERE id=$1 AND status IN ('QUEUED','RUNNING')",
        *values,
    )


async def fail_exhausted_tool_calls(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE agent.agent_tool_call SET status='FAILED',error_code='LEASE_RECOVERY_EXHAUSTED',completed_at=now() WHERE agent_run_id=$1 AND status='STARTED'",
        *values,
    )


async def find_available_job(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT * FROM ops.job WHERE job_kind IN ('PREPARE','AGENT_RUN')\n              AND ((status IN ('QUEUED','RETRY_WAIT') AND available_at<=clock_timestamp())\n               OR (status='RUNNING' AND lease_expires_at<clock_timestamp()))\n              AND attempt_count<max_attempts ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1",
        *values,
    )


async def claim_job(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "UPDATE ops.job SET status='RUNNING',attempt_count=attempt_count+1,\n              lease_owner=$2,lease_token=$3,lease_epoch=lease_epoch+1,\n              lease_expires_at=clock_timestamp()+make_interval(secs=>$4),heartbeat_at=clock_timestamp()\n              WHERE id=$1 RETURNING *",
        *values,
    )


async def get_locked_lease(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT *,lease_expires_at>clock_timestamp() AS lease_is_live FROM ops.job WHERE id=$1 FOR UPDATE",
        *values,
    )


async def renew_lease(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE ops.job SET heartbeat_at=clock_timestamp(),\n                  lease_expires_at=clock_timestamp()+make_interval(secs=>$4)\n                  WHERE id=$1 AND lease_token=$2 AND lease_epoch=$3 AND status='RUNNING'\n                  AND lease_expires_at>clock_timestamp()",
        *values,
    )


async def schedule_job_outcome(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE ops.job SET status=$2,last_error_code=$3,available_at=clock_timestamp()+make_interval(secs=>$4),lease_expires_at=NULL WHERE id=$1",
        *values,
    )


async def fail_preparation(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.prepare_run SET status=$2,error_code=$3,error_summary=$4,completed_at=now() WHERE id=$1 AND status<>'SUPERSEDED'",
        *values,
    )


async def get_job_diagnostics(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT j.status,j.last_error_code,coalesce(p.launch_context_id,d.launch_context_id) AS context_id "
        "FROM ops.job j LEFT JOIN clinical.prepare_run p ON p.id=j.prepare_run_id "
        "LEFT JOIN agent.agent_run a ON a.id=j.agent_run_id "
        "LEFT JOIN clinical.decision_run d ON d.id=a.decision_run_id WHERE j.id=$1",
        *values,
    )
