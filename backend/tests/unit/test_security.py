import time
from uuid import uuid4

import pytest

from chemo_agent_product.core.config import Settings
from chemo_agent_product.core.security import (
    BusinessError,
    Principal,
    issue_test_token,
    require_role,
    verify_test_token,
)


def test_signed_scope_and_role_cannot_be_changed():
    identity = Principal(
        subject="contract-doctor",
        hospital_id=uuid4(),
        staff_id=uuid4(),
        roles=["DOCTOR"],
        expires_at=int(time.time()) + 60,
    )
    token = issue_test_token(identity, "k" * 32)
    assert verify_test_token(token, "k" * 32, "test") == identity
    with pytest.raises(BusinessError) as denied:
        require_role(identity, "KNOWLEDGE_REVIEWER")
    assert denied.value.status == 403
    with pytest.raises(BusinessError):
        verify_test_token(token + "x", "k" * 32, "test")


def test_local_token_never_accepted_in_production():
    with pytest.raises(BusinessError) as denied:
        verify_test_token("anything", "k" * 32, "production")
    assert denied.value.code == "HOSPITAL_AUTH_NOT_CONFIGURED"


def test_expired_token_and_missing_key_rejected():
    principal = Principal(
        subject="contract", hospital_id=uuid4(), staff_id=uuid4(), roles=[], expires_at=1
    )
    with pytest.raises(BusinessError) as denied:
        verify_test_token(issue_test_token(principal, "k" * 32), "k" * 32, "test")
    assert denied.value.status == 401
    with pytest.raises(BusinessError) as missing:
        verify_test_token("anything", None, "test")
    assert missing.value.status == 503


def test_model_config_defaults_to_off_and_never_exposes_key():
    settings = Settings(_env_file=None, model_api_key="contract-placeholder", model_name="model")
    assert not settings.model_configured
    assert "contract-placeholder" not in str(settings)
    assert settings.model_copy(update={"model_enabled": True}).model_configured
