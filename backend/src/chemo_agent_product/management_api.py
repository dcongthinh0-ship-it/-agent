"""Separate, role-checked knowledge and operations entrances. No patient text or secrets."""

from __future__ import annotations

from fastapi import APIRouter

from chemo_agent_product.patient_api import PrincipalDep, WorkflowDep
from chemo_agent_product.security import require_role

router = APIRouter(prefix="/api/v1")


@router.get("/knowledge/overview")
async def knowledge_overview(p: PrincipalDep, w: WorkflowDep):
    require_role(p, "KNOWLEDGE_REVIEWER")
    async with w.pool.acquire() as c, c.transaction(readonly=True):
        evidence = await c.fetch(
            """SELECT e.source_code AS source,e.internal_level,e.evidence_grade,
            e.status,count(*) AS count
            FROM knowledge.evidence_record_version e
            GROUP BY e.source_code,e.internal_level,e.evidence_grade,e.status
            ORDER BY e.source_code,e.internal_level NULLS LAST,e.status"""
        )
        applicability = await c.fetch(
            "SELECT status,count(*) AS count FROM knowledge.regimen_applicability_version "
            "GROUP BY status"
        )
        packages = await c.fetch(
            "SELECT package_key,status,count(*) AS count FROM knowledge.rule_package_version "
            "GROUP BY package_key,status"
        )
        unmapped = await c.fetchval(
            """SELECT count(*) FROM knowledge.evidence_record_version e
            WHERE NOT EXISTS (SELECT 1 FROM knowledge.regimen_evidence_link a
            WHERE a.evidence_record_version_id=e.id)"""
        )
    return {
        "mode": "REVIEW_OVERVIEW",
        "evidence": [dict(row) for row in evidence],
        "applicability": [dict(row) for row in applicability],
        "rule_packages": [dict(row) for row in packages],
        "unlinked_evidence": unmapped,
        "publication": "REQUIRES_RECORDED_REVIEW",
    }


@router.get("/operations/overview")
async def operations_overview(p: PrincipalDep, w: WorkflowDep):
    require_role(p, "OPERATOR")
    async with w.pool.acquire() as c, c.transaction(readonly=True):
        jobs = await c.fetch(
            "SELECT job_kind,status,count(*) AS count FROM ops.job "
            "WHERE hospital_id=$1 GROUP BY job_kind,status",
            p.hospital_id,
        )
        preparations = await c.fetch(
            """SELECT pr.status,pr.stage,pr.error_code,count(*) AS count
            FROM clinical.prepare_run pr
            JOIN clinical.launch_context lc ON lc.id=pr.launch_context_id
            WHERE lc.hospital_id=$1 GROUP BY pr.status,pr.stage,pr.error_code""",
            p.hospital_id,
        )
        agents = await c.fetch(
            """SELECT r.status,r.error_code,count(*) AS count FROM agent.agent_run r
            JOIN clinical.decision_run d ON d.id=r.decision_run_id
            JOIN clinical.launch_context lc ON lc.id=d.launch_context_id
            WHERE lc.hospital_id=$1 GROUP BY r.status,r.error_code""",
            p.hospital_id,
        )
        attempts = await c.fetch(
            """SELECT operation_name,transport_outcome,count(*) AS count
            FROM integration.hospital_call_attempt WHERE hospital_id=$1
            GROUP BY operation_name,transport_outcome""",
            p.hospital_id,
        )
    return {
        "mode": "HOSPITAL_SCOPED_OVERVIEW",
        "jobs": [dict(row) for row in jobs],
        "preparations": [dict(row) for row in preparations],
        "agents": [dict(row) for row in agents],
        "hospital_calls": [dict(row) for row in attempts],
        "model_configured": w.settings.model_configured,
        "hospital_read_configured": bool(w.settings.hospital_adapter_config),
        "hospital_delivery_configured": bool(w.settings.hospital_delivery_config),
        "clinical_release": "NOT_ENABLED",
    }
