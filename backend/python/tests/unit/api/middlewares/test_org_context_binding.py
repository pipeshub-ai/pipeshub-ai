"""OrgContext is bound from the verified token only."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.api.middlewares import auth as auth_module
from app.api.middlewares.auth import (
    authMiddleware,
    require_service_token,
    service_token_org,
)
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.config.constants.service import TokenScopes
from app.core.org_context import MissingOrgContext, current, current_or_system

JWT_SECRET = "session-secret-for-tests"
SCOPED_SECRET = "scoped-secret-for-tests"
_after_response: list[str] = []


class _ConfigService:
    async def get_config(self, key, **kwargs) -> dict[str, str]:
        return {"jwtSecret": JWT_SECRET, "scopedJwtSecret": SCOPED_SECRET}


def _org() -> dict:
    c = current()
    return {"org": c.org_id, "user": c.user_id, "kind": c.kind, "source": c.source}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test-org-context"), config_service=_ConfigService
    )

    # the global-middleware shape used by query and connectors
    @app.middleware("http")
    async def authenticate(request: Request, call_next) -> object:
        try:
            await authMiddleware(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    @app.get("/async")
    async def async_route(orgId: str | None = None) -> dict:
        return _org()

    @app.get("/sync")
    def sync_route() -> dict:
        return _org()

    @app.get("/stream")
    async def stream_route() -> StreamingResponse:
        async def gen() -> AsyncGenerator[str, None]:
            await asyncio.sleep(0)
            yield _org()["org"]

        return StreamingResponse(gen())

    @app.get("/background")
    async def background_route(tasks: BackgroundTasks) -> dict:
        tasks.add_task(lambda: _after_response.append(current().org_id))
        return {}

    @app.post("/internal")
    async def internal_route(body: dict, claims: dict = Depends(require_service_token(TokenScopes.FETCH_CONFIG))) -> dict:
        service_token_org(claims, body.get("org_id"))
        return _org()

    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_build_app())


@pytest.fixture(autouse=True)
def _node_confirms_sessions() -> Iterator[None]:
    auth_module._session_checks.clear()
    with patch(
        "app.api.middlewares.auth.fetch_caller_role",
        new_callable=AsyncMock,
        return_value=CallerRole(CallerRoleStatus.VALID, "member"),
    ):
        yield
    auth_module._session_checks.clear()


def _auth(claims: dict, secret: str = JWT_SECRET) -> dict:
    now = int(time.time())
    return {"Authorization": "Bearer " + jwt.encode({"iat": now, "exp": now + 3600, **claims}, secret, algorithm="HS256")}


_SESSION = {"userId": "user-a", "orgId": "org_a", "role": "member"}


def test_org_comes_from_the_token_never_from_query_or_headers(client) -> None:
    r = client.get("/async?orgId=org_b", headers={**_auth(_SESSION), "X-Org-Id": "org_b", "orgId": "org_b"})
    assert r.json() == {"org": "org_a", "user": "user-a", "kind": "user", "source": "jwt"}


@pytest.mark.parametrize("path", ["/async", "/sync"])
def test_async_and_threadpool_endpoints_keep_the_context(client, path) -> None:
    assert client.get(path, headers=_auth(_SESSION)).json()["org"] == "org_a"


def test_streaming_endpoint_keeps_the_context(client) -> None:
    assert client.get("/stream", headers=_auth(_SESSION)).text == "org_a"


def test_background_task_sees_the_context_it_was_spawned_with(client) -> None:
    _after_response.clear()
    client.get("/background", headers=_auth(_SESSION))
    assert _after_response == ["org_a"]


def test_concurrent_requests_keep_their_own_org(client) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def hit(i: int) -> tuple[str, str]:
        org = f"org_{i % 3}"
        return org, client.get("/async", headers=_auth({**_SESSION, "orgId": org})).json()["org"]

    # One portal (one event loop), as in production: the session-check cache holds futures bound to a loop.
    with client, ThreadPoolExecutor(8) as ex:
        assert all(want == got for want, got in ex.map(hit, range(60)))


def test_scoped_token_naming_another_org_is_403_and_context_stays_the_tokens(client) -> None:
    scoped = _auth({"userId": "svc", "orgId": "org_a", "scopes": [TokenScopes.FETCH_CONFIG.value]}, SCOPED_SECRET)
    assert client.post("/internal", json={"org_id": "org_b"}, headers=scoped).status_code == 403
    ok = client.post("/internal", json={"org_id": "org_a"}, headers=scoped).json()
    assert (ok["org"], ok["kind"], ok["source"]) == ("org_a", "service", "scoped_jwt")


def test_scoped_token_without_org_is_401_except_on_the_pre_org_allowlist(client) -> None:
    scoped = _auth({"userId": "system", "scopes": [TokenScopes.FETCH_CONFIG.value]}, SCOPED_SECRET)
    assert client.post("/internal", json={}, headers=scoped).status_code == 401
    assert client.get("/async", headers=scoped).status_code == 401


def test_pre_org_allowlist_is_exactly_the_documented_routes() -> None:
    # Adding a route here is a security decision: update docs/org-context.md in the same change.
    assert auth_module.PRE_ORG_SERVICE_ROUTES == frozenset({"/api/v1/parse/providers"})


def test_allowlisted_route_accepts_an_orgless_token_unbound() -> None:
    app = FastAPI()
    app.container = SimpleNamespace(logger=lambda: logging.getLogger("t"), config_service=_ConfigService)
    app.get("/api/v1/parse/providers", dependencies=[Depends(authMiddleware)])(lambda: {"ok": True})
    scoped = _auth({"userId": "system", "scopes": [TokenScopes.FETCH_CONFIG.value]}, SCOPED_SECRET)
    assert TestClient(app).get("/api/v1/parse/providers", headers=scoped).json() == {"ok": True}


def test_session_token_without_org_stays_unbound_and_fails_closed(client) -> None:
    with pytest.raises(MissingOrgContext):
        client.get("/async", headers=_auth({"userId": "u", "role": "member"}))


def test_per_router_dependency_binds_for_sync_and_async_endpoints() -> None:
    app = FastAPI()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test-org-context"), config_service=_ConfigService
    )
    app.get("/a", dependencies=[Depends(authMiddleware)])(lambda: _org())


    async def _async_org() -> dict:
        return _org()

    app.get("/b", dependencies=[Depends(authMiddleware)])(_async_org)
    c = TestClient(app)
    assert c.get("/a", headers=_auth(_SESSION)).json()["org"] == "org_a"
    assert c.get("/b", headers=_auth(_SESSION)).json()["org"] == "org_a"


def test_no_context_outside_a_request() -> None:
    with pytest.raises(MissingOrgContext):
        current_or_system()
