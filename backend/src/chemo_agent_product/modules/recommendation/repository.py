"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg


async def get_current_candidate(c: asyncpg.Connection, *values):
    return await c.fetchrow(
        "SELECT dc.*,t.template_payload,t.content_hash AS template_hash,\n          dr.snapshot_id,dr.knowledge_manifest FROM clinical.decision_candidate dc\n          JOIN clinical.decision_run dr ON dr.id=dc.decision_run_id AND dr.status='SUCCEEDED'\n          JOIN catalog_bridge.template_version_reference t ON t.id=dc.template_ref_id\n          WHERE dc.id=$1 AND dr.launch_context_id=$2 AND dr.prepare_run_id=$3",
        *values,
    )


async def get_snapshot_payload(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1", *values
    )


async def get_fixed_evidence(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM knowledge.evidence_record_version WHERE id=$1", *values)
