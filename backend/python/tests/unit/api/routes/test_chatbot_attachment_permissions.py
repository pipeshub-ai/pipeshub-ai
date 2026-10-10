from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.middlewares.auth import AUTH_POLICY_ATTR
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes import chatbot as chatbot_module
from app.api.routes.chatbot import router, validate_attachment_ids
from app.config.constants.service import TokenScopes

_CLAIMS = {"userId": "owner", "orgId": "org-1"}


def _record(record_id, connector="ATTACHMENTS", org="org-1"):
    return {"_key": record_id, "connectorName": connector, "orgId": org}


def _graph(records, edges=None):
    edges = dict(edges or {})
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(side_effect=lambda user_id: {"_key": f"key-{user_id}"})
    gp.get_edge = AsyncMock(
        side_effect=lambda **kw: (
            {"role": edges[(kw["from_id"], kw["to_id"])]}
            if (kw["from_id"], kw["to_id"]) in edges
            else None
        )
    )
    gp.batch_create_edges = AsyncMock()
    gp.batch_delete_edges = AsyncMock()
    gp.get_records_by_record_ids = AsyncMock(return_value=records)
    return gp


def _req(body):
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


def test_ph07_19_grant_and_revoke_routes_are_gone():
    # Supersedes SEC-08 and PH01-03: chat content is authorised by the PDP at read
    # time (can_read_record), so Node no longer writes or revokes READER edges.
    permission_routes = [
        (sorted(r.methods), r.path)
        for r in router.routes
        if isinstance(r, APIRoute) and r.path.endswith("/permissions")
    ]
    assert permission_routes == []
    for name in (
        "grant_attachment_permissions",
        "revoke_attachment_permissions",
        "grant_artifact_permissions",
        "revoke_artifact_permissions",
        "AttachmentPermissionRequest",
        "ArtifactPermissionRequest",
        "_grant_reader_permissions",
        "_revoke_reader_permissions",
    ):
        assert not hasattr(chatbot_module, name), name


def test_ph07_19_validate_route_is_kept():
    assert any(
        isinstance(r, APIRoute) and r.path == "/chat/attachments/validate" for r in router.routes
    )


@pytest.mark.asyncio
async def test_validate_returns_only_owned_attachments_in_org():
    gp = _graph(
        [_record("mine"), _record("theirs"), _record("drive", connector="GOOGLE_DRIVE")],
        {
            ("key-owner", "mine"): "OWNER",
            ("key-other", "theirs"): "OWNER",
            ("key-owner", "drive"): "OWNER",
        },
    )

    out = await validate_attachment_ids(
        _req({"recordIds": ["mine", "theirs", "drive", "missing"]}), gp, _CLAIMS
    )

    assert out == {"recordIds": ["mine"]}


@pytest.mark.asyncio
async def test_validate_requires_user_and_org_in_token():
    with pytest.raises(HTTPException) as exc:
        await validate_attachment_ids(_req({"recordIds": ["a"]}), _graph([]), {"orgId": "org-1"})
    assert exc.value.status_code == 400


def test_ph01_05_validate_route_requires_conversation_permissions_service_token():
    route = next(
        r
        for r in router.routes
        if isinstance(r, APIRoute)
        and r.path.endswith("/chat/attachments/validate")
        and "POST" in r.methods
    )
    policies = [
        getattr(dep.call, AUTH_POLICY_ATTR)
        for dep in route.dependant.dependencies
        if hasattr(dep.call, AUTH_POLICY_ATTR)
    ]
    assert [p.kind for p in policies] == ["service"]
    assert policies[0].service_scopes == {TokenScopes.CONVERSATION_PERMISSIONS.value}


@pytest.mark.asyncio
async def test_validate_service_account_keeps_org_attachments_without_owner_edge():
    gp = _graph(
        [_record("slack-file"), _record("other-org", org="org-2"), _record("drive", connector="GOOGLE_DRIVE")]
    )
    gp.get_user_by_user_id = AsyncMock(return_value=None)

    out = await validate_attachment_ids(
        _req({"recordIds": ["slack-file", "other-org", "drive", "missing"]}),
        gp,
        {**_CLAIMS, "isServiceAccount": True},
    )

    assert out == {"recordIds": ["slack-file"]}
    gp.get_edge.assert_not_called()


@pytest.mark.asyncio
async def test_validate_without_service_account_claim_still_requires_owner_edge():
    gp = _graph([_record("slack-file")])
    gp.get_user_by_user_id = AsyncMock(return_value=None)

    out = await validate_attachment_ids(
        _req({"recordIds": ["slack-file"]}), gp, {**_CLAIMS, "isServiceAccount": False}
    )

    assert out == {"recordIds": []}


def test_validate_request_rejects_more_than_50_record_ids():
    from pydantic import ValidationError

    from app.api.routes.chatbot import AttachmentValidateRequest

    AttachmentValidateRequest(recordIds=[str(i) for i in range(50)])
    with pytest.raises(ValidationError):
        AttachmentValidateRequest(recordIds=[str(i) for i in range(51)])


_JWT_SECRET = "session-secret-for-tests"
_SCOPED_SECRET = "scoped-secret-for-tests"


def _validate_client(gp):
    import logging
    import time
    from types import SimpleNamespace

    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from jose import jwt

    from app.api.middlewares.auth import authMiddleware
    from app.api.routes.chatbot import get_graph_provider

    class _Config:
        async def get_config(self, key, **kwargs):
            return {"jwtSecret": _JWT_SECRET, "scopedJwtSecret": _SCOPED_SECRET}

    app = FastAPI()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test-attachment-validate"),
        config_service=_Config,
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        try:
            await authMiddleware(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(router)
    app.dependency_overrides[get_graph_provider] = lambda: gp

    def sign(claims, secret):
        now = int(time.time())
        return jwt.encode({"iat": now, "exp": now + 300, **claims}, secret, algorithm="HS256")

    return TestClient(app), sign


def test_validate_route_rejects_regular_user_jwt_and_accepts_service_token():
    gp = _graph([_record("mine")], {("key-owner", "mine"): "OWNER"})
    client, sign = _validate_client(gp)
    path = next(
        r.path
        for r in router.routes
        if isinstance(r, APIRoute) and r.path.endswith("/chat/attachments/validate")
    )
    body = {"recordIds": ["mine"]}

    user_jwt = sign({"userId": "owner", "orgId": "org-1", "role": "member"}, _JWT_SECRET)
    # Main asks Node whether a session token is still live (#3680); this one is.
    with patch(
        "app.api.middlewares.auth.fetch_caller_role",
        new=AsyncMock(return_value=CallerRole(CallerRoleStatus.VALID, "member")),
    ):
        denied = client.post(path, json=body, headers={"Authorization": f"Bearer {user_jwt}"})
    assert denied.status_code == 403
    gp.get_records_by_record_ids.assert_not_called()

    service_jwt = sign(
        {**_CLAIMS, "scopes": [TokenScopes.CONVERSATION_PERMISSIONS.value]}, _SCOPED_SECRET
    )
    allowed = client.post(path, json=body, headers={"Authorization": f"Bearer {service_jwt}"})
    assert allowed.status_code == 200
    assert allowed.json() == {"recordIds": ["mine"]}
