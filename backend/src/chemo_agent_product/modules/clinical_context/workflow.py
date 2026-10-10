from __future__ import annotations

from uuid import UUID

import asyncpg

from chemo_agent_product.core.commands import CommandService
from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.security import Principal
from chemo_agent_product.modules.clinical_context.schemas import LaunchInput
from chemo_agent_product.modules.clinical_context.service import ClinicalContextService
from chemo_agent_product.modules.patient_regimen.schemas import ConfirmInput, SaveInput
from chemo_agent_product.modules.patient_regimen.service import PatientRegimenService
from chemo_agent_product.modules.recommendation.service import CandidateService


class Workflow:
    """Patient workflow facade; delegates use cases without opening extra transactions."""

    def __init__(self, pool: asyncpg.Pool, settings: Settings):
        self.pool, self.settings = pool, settings
        self.contexts = ClinicalContextService(self)
        self.candidates = CandidateService(self)
        self.patient_regimens = PatientRegimenService(self)
        self.commands = CommandService(self)

    async def scoped_context(
        self,
        c: asyncpg.Connection,
        context_id: UUID,
        p: Principal,
        active: bool = True,
        lock: bool = False,
    ):
        return await self.contexts.scoped_context(c, context_id, p, active, lock)

    async def prepare(self, c, context, p, generation):
        return await self.contexts.prepare(c, context, p, generation)

    async def launch(self, p: Principal, request: LaunchInput, key: str):
        return await self.contexts.launch(p, request, key)

    async def preparation_status(self, p: Principal, context_id: UUID):
        return await self.contexts.preparation_status(p, context_id)

    async def read_context(self, p: Principal, context_id: UUID):
        return await self.contexts.read_context(p, context_id)

    async def refresh(self, p, context_id, key, expected_generation):
        return await self.contexts.refresh(p, context_id, key, expected_generation)

    async def candidate(self, c, context_id, p, candidate_id):
        return await self.candidates.candidate(c, context_id, p, candidate_id)

    async def detail(self, p, context_id, candidate_id):
        return await self.candidates.detail(p, context_id, candidate_id)

    async def candidate_evidence(self, p, context_id, candidate_id):
        return await self.candidates.candidate_evidence(p, context_id, candidate_id)

    async def select(self, p, context_id, candidate_id, key):
        return await self.patient_regimens.select(p, context_id, candidate_id, key)

    async def instance(self, c, context_id, p, instance_id, lock=False):
        return await self.patient_regimens.instance(c, context_id, p, instance_id, lock)

    def initial_fields(self, template, snapshot):
        return self.patient_regimens.initial_fields(template, snapshot)

    async def read_instance(self, p, context_id, instance_id, revision_id=None):
        return await self.patient_regimens.read_instance(p, context_id, instance_id, revision_id)

    async def save(self, p, context_id, instance_id, request: SaveInput, key):
        return await self.patient_regimens.save(p, context_id, instance_id, request, key)

    async def confirm(self, p, context_id, instance_id, request: ConfirmInput, key):
        return await self.patient_regimens.confirm(p, context_id, instance_id, request, key)

    async def command(self, c, p, operation, key, payload):
        return await self.commands.command(c, p, operation, key, payload)

    async def complete(self, c, command_id, response, resource_type, resource_id):
        return await self.commands.complete(c, command_id, response, resource_type, resource_id)

    async def audit(self, c, p, context, event, aggregate_id, summary):
        return await self.commands.audit(c, p, context, event, aggregate_id, summary)

    async def action(self, p, context_id, request, idempotency_key):
        return await self.candidates.action(p, context_id, request, idempotency_key)

    async def confirm_with_review(self, p, context_id, instance_id, request, idempotency_key):
        return await self.patient_regimens.confirm_with_review(
            p, context_id, instance_id, request, idempotency_key
        )
