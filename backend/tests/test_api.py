from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from chemo_agent_product.api import create_app
from chemo_agent_product.config import Settings
from chemo_agent_product.contracts import (
    ContextReadout,
    EvidenceDetail,
    EvidenceList,
    RegimenDetail,
    RegimenPage,
)

REGIMEN_ID = UUID("00000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("00000000-0000-0000-0000-000000000002")
EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000003")
CONTEXT_ID = UUID("00000000-0000-0000-0000-000000000004")


class TestReader:
    async def list_regimens(self, search: str, page: int, page_size: int) -> RegimenPage:
        return RegimenPage(items=[], total=0, page=page, page_size=page_size)

    async def get_regimen(self, regimen_id: UUID, version_id: UUID) -> RegimenDetail | None:
        return None

    async def list_evidence(self, regimen_id: UUID, version_id: UUID) -> EvidenceList | None:
        return None

    async def get_evidence(self, evidence_id: UUID) -> EvidenceDetail | None:
        return None


class TestContextReader:
    async def get_context(self, context_id: UUID) -> ContextReadout | None:
        if context_id != CONTEXT_ID:
            return None
        return ContextReadout(
            context_id=context_id,
            context_state="ACTIVE",
            expires_at="2099-01-01T00:00:00+00:00",
            prepare_run_id=UUID("00000000-0000-0000-0000-000000000005"),
            prepare_status="RUNNING",
            prepare_stage="FETCHING",
            prepare_error_code=None,
            decision_run_id=None,
            decision_status=None,
            outcome_code=None,
            patient_ref="SYNTH_PATIENT_1",
            encounter_ref="SYNTH_ENCOUNTER_1",
            candidates=[],
        )


@pytest.mark.asyncio
async def test_local_catalog_contract_and_not_found() -> None:
    app = create_app(Settings(environment="test"), reader=TestReader())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            status = await client.get("/api/v1/status")
            result = await client.get("/api/v1/regimens?page=2&page_size=10")
            missing = await client.get(f"/api/v1/evidence/{EVIDENCE_ID}")
            invalid = await client.get("/api/v1/regimens?page_size=1000")
    assert status.json()["mode"] == "READ_ONLY_TEST"
    assert status.json()["model"] == "NOT_CONNECTED"
    assert result.json() == {"items": [], "total": 0, "page": 2, "page_size": 10}
    assert missing.status_code == 404
    assert missing.json()["code"] == "EVIDENCE_NOT_FOUND"
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_production_read_api_refuses_without_trusted_authentication() -> None:
    app = create_app(Settings(environment="production"), reader=TestReader())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/regimens")
            status = await client.get("/api/v1/status")
    assert response.status_code == 503
    assert response.json()["code"] == "CAPABILITY_NOT_APPROVED"
    assert status.json()["mode"] == "NOT_APPROVED"


@pytest.mark.asyncio
async def test_unconfigured_database_has_explicit_failure() -> None:
    app = create_app(Settings(environment="test", database_url=None))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/regimens")
            status = await client.get("/api/v1/status")
    assert response.status_code == 503
    assert response.json()["code"] == "CATALOG_UNAVAILABLE"
    assert status.json()["database"] == "UNCONFIGURED"


@pytest.mark.asyncio
async def test_test_only_context_readout_is_separate_from_catalog() -> None:
    app = create_app(
        Settings(environment="test"),
        reader=TestReader(),
        context_reader=TestContextReader(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get(f"/api/v1/contexts/{CONTEXT_ID}")
            missing = await client.get("/api/v1/contexts/00000000-0000-0000-0000-000000000006")
            status = await client.get("/api/v1/status")
    assert result.status_code == 200
    assert result.json()["mode"] == "TEST_ONLY"
    assert result.json()["prepare_status"] == "RUNNING"
    assert result.json()["candidates"] == []
    assert missing.status_code == 404
    assert status.json()["patient_context"] == "TEST_ONLY"


@pytest.mark.asyncio
async def test_context_id_is_not_authentication_in_production() -> None:
    app = create_app(Settings(environment="production"), context_reader=TestContextReader())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/contexts/{CONTEXT_ID}")
    assert response.status_code == 503
    assert response.json()["code"] == "CAPABILITY_NOT_APPROVED"
