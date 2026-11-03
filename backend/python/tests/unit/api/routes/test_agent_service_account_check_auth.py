"""The service-agent check route, exercised through the real auth middleware.

The gateway asks ``GET /api/v1/agent/{id}/internal/service-account`` before it
lets a Slack caller without an account chat with a service agent. Other tests
of this route patch ``_get_user_context``, so they could not see that the
gateway's token was refused before the route ran. These use real signed tokens
with the exact claims the gateway mints.
"""

import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from app.api.middlewares.auth import authMiddleware
from app.api.routes.agent import router as agent_router

JWT_SECRET = "session-secret-for-tests-0123456789abcdef"
SCOPED_SECRET = "scoped-secret-for-tests-0123456789abcdef"
ORG_ID = "65f0c0ffee0000000000aaaa"
EMAIL = "outsider@example.com"


class _ConfigService:
    async def get_config(self, key: str, **kwargs: object) -> dict:
        return {"jwtSecret": JWT_SECRET, "scopedJwtSecret": SCOPED_SECRET}


def _stand_in_user_id(email: str) -> str:
    """Mirror of stableObjectIdHexForExternalEmail in the Node gateway."""
    return hashlib.sha256(f"slack-service-account:{email.lower().strip()}".encode()).hexdigest()[:24]


def _sign(claims: dict) -> str:
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + 60, **claims}, SCOPED_SECRET, algorithm="HS256")


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test-sa-check-auth"),
        config_service=_ConfigService,
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable) -> Response:
        try:
            await authMiddleware(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(agent_router, prefix="/api/v1/agent")

    agents = {
        "sa": {"_key": "sa", "isServiceAccount": True, "createdBy": "creator"},
        "normal": {"_key": "normal", "isServiceAccount": False, "createdBy": "creator"},
    }
    graph = MagicMock()
    graph.get_agent = AsyncMock(side_effect=lambda key: agents.get(key))
    graph.get_document = AsyncMock(return_value={"_key": "creator", "orgId": ORG_ID})
    services = {"graph_provider": graph, "config_service": MagicMock(), "logger": MagicMock()}
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock):
        yield TestClient(app)


def _check(client: TestClient, agent: str, token: str) -> int:
    return client.get(
        f"/api/v1/agent/{agent}/internal/service-account",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code


GATEWAY_CLAIMS = {
    "userId": _stand_in_user_id(EMAIL),
    "orgId": ORG_ID,
    "email": EMAIL,
    "scopes": ["conversation:create"],
}


def test_gateway_check_token_confirms_a_service_agent(client) -> None:
    assert _check(client, "sa", _sign(GATEWAY_CLAIMS)) == 200


def test_gateway_check_token_is_refused_for_a_normal_agent(client) -> None:
    assert _check(client, "normal", _sign(GATEWAY_CLAIMS)) == 403


def test_gateway_check_token_gets_404_for_another_orgs_agent(client) -> None:
    other_org = {**GATEWAY_CLAIMS, "orgId": "65f0c0ffee0000000000bbbb"}
    assert _check(client, "sa", _sign(other_org)) == 404


def test_the_slack_bot_token_alone_is_refused(client) -> None:
    """What the gateway used to forward: email and scope, no org or user."""
    assert _check(client, "sa", _sign({"email": EMAIL, "scopes": ["conversation:create"]})) == 401


def test_a_token_without_a_user_is_refused(client) -> None:
    no_user = {k: v for k, v in GATEWAY_CLAIMS.items() if k != "userId"}
    assert _check(client, "sa", _sign(no_user)) == 401
