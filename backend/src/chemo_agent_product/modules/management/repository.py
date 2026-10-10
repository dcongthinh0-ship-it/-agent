"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg


async def summarize_evidence(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT e.source_code AS source,e.internal_level,e.evidence_grade,\n            e.status,count(*) AS count\n            FROM knowledge.evidence_record_version e\n            GROUP BY e.source_code,e.internal_level,e.evidence_grade,e.status\n            ORDER BY e.source_code,e.internal_level NULLS LAST,e.status",
        *values,
    )


async def summarize_applicability(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT status,count(*) AS count FROM knowledge.regimen_applicability_version GROUP BY status",
        *values,
    )


async def summarize_rule_packages(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT package_key,status,count(*) AS count FROM knowledge.rule_package_version GROUP BY package_key,status",
        *values,
    )


async def count_unlinked_evidence(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT count(*) FROM knowledge.evidence_record_version e\n            WHERE NOT EXISTS (SELECT 1 FROM knowledge.regimen_evidence_link a\n            WHERE a.evidence_record_version_id=e.id)",
        *values,
    )


async def summarize_jobs(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT job_kind,status,count(*) AS count FROM ops.job WHERE hospital_id=$1 GROUP BY job_kind,status",
        *values,
    )


async def summarize_preparations(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT pr.status,pr.stage,pr.error_code,count(*) AS count\n            FROM clinical.prepare_run pr\n            JOIN clinical.launch_context lc ON lc.id=pr.launch_context_id\n            WHERE lc.hospital_id=$1 GROUP BY pr.status,pr.stage,pr.error_code",
        *values,
    )


async def summarize_agents(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT r.status,r.error_code,count(*) AS count FROM agent.agent_run r\n            JOIN clinical.decision_run d ON d.id=r.decision_run_id\n            JOIN clinical.launch_context lc ON lc.id=d.launch_context_id\n            WHERE lc.hospital_id=$1 GROUP BY r.status,r.error_code",
        *values,
    )


async def summarize_hospital_calls(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT operation_name,transport_outcome,count(*) AS count\n            FROM integration.hospital_call_attempt WHERE hospital_id=$1\n            GROUP BY operation_name,transport_outcome",
        *values,
    )
