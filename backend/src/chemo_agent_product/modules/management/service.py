"""Separate, role-checked knowledge and operations entrances. No patient text or secrets."""

from __future__ import annotations

from chemo_agent_product.core.security import Principal, require_role
from chemo_agent_product.modules.clinical_context.workflow import Workflow

from . import repository


async def knowledge_overview(p: Principal, w: Workflow):
    require_role(p, "KNOWLEDGE_REVIEWER")
    async with w.pool.acquire() as c, c.transaction(readonly=True):
        evidence = await repository.summarize_evidence(c)
        applicability = await repository.summarize_applicability(c)
        packages = await repository.summarize_rule_packages(c)
        unmapped = await repository.count_unlinked_evidence(c)
    return {
        "mode": "REVIEW_OVERVIEW",
        "evidence": [dict(row) for row in evidence],
        "applicability": [dict(row) for row in applicability],
        "rule_packages": [dict(row) for row in packages],
        "unlinked_evidence": unmapped,
        "publication": "REQUIRES_RECORDED_REVIEW",
    }


async def operations_overview(p: Principal, w: Workflow):
    require_role(p, "OPERATOR")
    async with w.pool.acquire() as c, c.transaction(readonly=True):
        jobs = await repository.summarize_jobs(c, p.hospital_id)
        preparations = await repository.summarize_preparations(c, p.hospital_id)
        agents = await repository.summarize_agents(c, p.hospital_id)
        attempts = await repository.summarize_hospital_calls(c, p.hospital_id)
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
