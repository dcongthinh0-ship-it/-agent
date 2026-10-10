from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from chemo_agent_product.core.security import BusinessError, Principal, verify_test_token
from chemo_agent_product.modules.clinical_context.workflow import Workflow


async def principal(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    settings = request.app.state.settings
    if not authorization or not authorization.startswith("Bearer "):
        raise BusinessError("AUTH_REQUIRED", "请从可信工作站入口进入当前患者", 401)
    return verify_test_token(
        authorization[7:],
        settings.launch_signing_key.get_secret_value() if settings.launch_signing_key else None,
        settings.environment,
    )


async def workflow(request: Request) -> Workflow:
    value = getattr(request.app.state, "workflow", None)
    if not value:
        raise BusinessError("WORKFLOW_NOT_CONFIGURED", "患者流程服务尚未配置", 503)
    return value


PrincipalDep = Annotated[Principal, Depends(principal)]

WorkflowDep = Annotated[Workflow, Depends(workflow)]

CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
