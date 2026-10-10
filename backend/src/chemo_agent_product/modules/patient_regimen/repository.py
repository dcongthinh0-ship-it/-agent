"""PostgreSQL operations; transactions and authorization belong to the service."""

import asyncpg

from chemo_agent_product.persistence.database import insert


async def insert_patient_regimen_instance(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.patient_regimen_instance", values)


async def insert_doctor_action_event(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.doctor_action_event", values)


async def get_scoped_instance(c: asyncpg.Connection, *values, lock=False):
    return await c.fetchrow(
        "SELECT i.*,t.template_payload,t.content_hash AS template_hash,t.availability_state,\n          cand.applicability_snapshot,cand.evidence_refs,cand.data_labels,cand.safety_labels,cand.evidence_state,\n          d.knowledge_manifest,d.snapshot_id AS origin_snapshot_id FROM clinical.patient_regimen_instance i\n          JOIN catalog_bridge.template_version_reference t ON t.id=i.template_ref_id\n          LEFT JOIN clinical.decision_candidate cand ON cand.id=i.origin_candidate_id\n          LEFT JOIN clinical.decision_run d ON d.id=cand.decision_run_id\n          WHERE i.id=$1 AND i.hospital_id=$2 AND i.patient_reference_id=$3 AND i.encounter_reference_id=$4"
        + (" FOR UPDATE OF i" if lock else ""),
        *values,
    )


async def find_instance_revision(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT id FROM clinical.patient_regimen_revision WHERE id=$1 AND instance_id=$2", *values
    )


async def get_revision(c: asyncpg.Connection, *values):
    return await c.fetchrow("SELECT * FROM clinical.patient_regimen_revision WHERE id=$1", *values)


async def get_snapshot_payload(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT clinical_payload FROM clinical.clinical_snapshot WHERE id=$1", *values
    )


async def list_revision_fields(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT field_key,value_json FROM clinical.patient_regimen_field_value WHERE revision_id=$1",
        *values,
    )


async def list_revision_history(c: asyncpg.Connection, *values):
    return await c.fetch(
        "SELECT id,revision_no,content_hash,created_at,change_reason FROM clinical.patient_regimen_revision WHERE instance_id=$1 ORDER BY revision_no DESC",
        *values,
    )


async def next_revision_number(c: asyncpg.Connection, *values):
    return await c.fetchval(
        "SELECT coalesce(max(revision_no),0)+1 FROM clinical.patient_regimen_revision WHERE instance_id=$1",
        *values,
    )


async def insert_patient_regimen_revision(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.patient_regimen_revision", values)


async def insert_patient_regimen_field_value(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.patient_regimen_field_value", values)


async def insert_patient_regimen_order_item(c: asyncpg.Connection, values: dict):
    return await insert(c, "clinical.patient_regimen_order_item", values)


async def set_current_revision(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.patient_regimen_instance SET current_revision_id=$2,current_confirmed_revision_id=NULL WHERE id=$1",
        *values,
    )


async def set_confirmed_revision(c: asyncpg.Connection, *values):
    return await c.execute(
        "UPDATE clinical.patient_regimen_instance SET current_confirmed_revision_id=$2 WHERE id=$1",
        *values,
    )
