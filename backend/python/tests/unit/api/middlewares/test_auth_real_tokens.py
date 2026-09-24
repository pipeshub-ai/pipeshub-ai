"""Token-class enforcement exercised with real signed JWTs (no decode mocks).

A minimal app wires the real auth middleware and route dependencies the way
query_main / connectors_main do, so these tests cover the whole path from the
Authorization header to the route decision.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jose import jwt

from app.api.middlewares.auth import (
    authMiddleware,
    deny_service_tokens,
    require_scopes,
    require_service_token,
)
from app.config.constants.service import OAuthScopes, TokenScopes

JWT_SECRET = "session-secret-for-tests"
SCOPED_SECRET = "scoped-secret-for-tests"
OTHER_SECRET = "a-secret-no-service-knows"


class _ConfigService:
    async def get_config(self, key, **kwargs):
        return {"jwtSecret": JWT_SECRET, "scopedJwtSecret": SCOPED_SECRET}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test-auth-real-tokens"),
        config_service=_ConfigService,
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        try:
            await authMiddleware(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    @app.get("/user", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
    async def user_route() -> dict:
        return {"ok": True}

    @app.get("/open", dependencies=[Depends(deny_service_tokens)])
    async def open_route() -> dict:
        return {"ok": True}

    @app.get(
        "/slack-chat",
        dependencies=[
            Depends(
                require_scopes(
                    OAuthScopes.AGENT_EXECUTE,
                    service_scopes=(TokenScopes.CONVERSATION_CREATE,),
                )
            )
        ],
    )
    async def slack_chat_route() -> dict:
        return {"ok": True}

    @app.get("/internal")
    async def internal_route(
        claims: dict = Depends(require_service_token(TokenScopes.FETCH_CONFIG)),
    ) -> dict:
        return {"orgId": claims["orgId"]}

    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_build_app())


def _sign(claims: dict, secret: str, ttl_seconds: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + ttl_seconds, **claims}, secret, algorithm="HS256")


def _node_user_action_secret(scoped_secret: str) -> str:
    """Mirror of deriveUserActionSecret (backend/nodejs/apps/src/libs/utils/jwtKeys.ts)."""
    return hmac.new(
        scoped_secret.encode(), b"pipeshub/jwt/user-action/v1", hashlib.sha256
    ).hexdigest()


def _status(client: TestClient, path: str, token: str) -> int:
    return client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code


_SESSION_CLAIMS = {"userId": "user-1", "orgId": "org-1", "role": "member"}
_USER_CLAIMS = {"userId": "user-1", "orgId": "org-1"}
_ROUTES = ("/user", "/open", "/slack-chat", "/internal")


def test_session_token_reaches_user_routes_only(client):
    token = _sign(_SESSION_CLAIMS, JWT_SECRET)
    assert _status(client, "/user", token) == 200
    assert _status(client, "/open", token) == 200
    assert _status(client, "/slack-chat", token) == 200
    assert _status(client, "/internal", token) == 403


@pytest.mark.parametrize(
    "scope",
    [
        "token:refresh",
        "password:reset",
        "email:validate",
        "org:email:verify",
        "email:verified",
        "mail:send",
        "user:lookup",
        "storage:token",
    ],
)
def test_node_only_tokens_signed_with_raw_scoped_secret_are_rejected(client, scope):
    """Tokens minted before the Node key split (or for Node-only use) never authenticate here."""
    token = _sign({**_USER_CLAIMS, "scopes": [scope]}, SCOPED_SECRET)
    for path in _ROUTES:
        assert _status(client, path, token) == 401, path


@pytest.mark.parametrize("scope", ["token:refresh", "password:reset", "fetch:config"])
def test_tokens_signed_with_node_user_action_key_are_rejected(client, scope):
    """Python never holds the derived key, so it cannot accept user-held tokens at all."""
    token = _sign({**_USER_CLAIMS, "scopes": [scope]}, _node_user_action_secret(SCOPED_SECRET))
    for path in _ROUTES:
        assert _status(client, path, token) == 401, path


def test_fetch_config_service_token_reaches_only_its_route(client):
    token = _sign({"userId": "system", "orgId": "system", "scopes": ["fetch:config"]}, SCOPED_SECRET)
    assert _status(client, "/internal", token) == 200
    assert _status(client, "/user", token) == 403
    assert _status(client, "/open", token) == 403
    assert _status(client, "/slack-chat", token) == 403


def test_conversation_create_token_reaches_only_opted_in_route(client):
    token = _sign(
        {**_USER_CLAIMS, "scopes": ["conversation:create"], "isServiceAccount": True},
        SCOPED_SECRET,
    )
    assert _status(client, "/slack-chat", token) == 200
    assert _status(client, "/user", token) == 403
    assert _status(client, "/internal", token) == 403


def test_service_token_claiming_admin_oauth_gets_no_oauth_access(client):
    token = _sign(
        {
            **_USER_CLAIMS,
            "scopes": ["conversation:create"],
            "tokenType": "oauth",
            "isOAuth": True,
            "oauthScopes": ["connector:read"],
            "role": "admin",
        },
        SCOPED_SECRET,
    )
    assert _status(client, "/user", token) == 403


def test_expired_service_token_is_rejected(client):
    token = _sign({**_USER_CLAIMS, "scopes": ["fetch:config"]}, SCOPED_SECRET, ttl_seconds=-60)
    assert _status(client, "/internal", token) == 401


def test_token_signed_with_unknown_secret_is_rejected(client):
    token = _sign({**_USER_CLAIMS, "scopes": ["fetch:config"]}, OTHER_SECRET)
    assert _status(client, "/internal", token) == 401


def test_unsigned_token_is_rejected(client):
    def _segment(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    now = int(time.time())
    token = ".".join(
        [
            _segment({"alg": "none", "typ": "JWT"}),
            _segment({**_USER_CLAIMS, "scopes": ["fetch:config"], "exp": now + 3600}),
            "",
        ]
    )
    assert _status(client, "/internal", token) == 401


def test_missing_authorization_header_is_rejected(client):
    assert client.get("/user").status_code == 401
