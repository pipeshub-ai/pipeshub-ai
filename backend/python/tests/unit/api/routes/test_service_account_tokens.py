"""A service account's own token names no person, so no agent-loop entry runs for it.

A service token is an OAuth access token the Node API mints for a service account
against the org's `svc-system:<orgId>` client, with `userId` the account's id. The
same account's delegated token (token exchange) carries `act.sub` = the account and
`userId` = the person it acts as, and is that person. The shapes come from
`backend/nodejs/apps/tests/modules/oauth_provider/fixtures/minted-token-shapes.json`,
which the Node suite checks against what its minting code really produces.

Every entry point that runs the agent loop is driven with each shape: the assistant
chat (`/chat`, `/chat/stream`), the universal agent and a named agent (`chat`,
`chat/stream`). "Reached the loop" is observed at the loop's own first step (its
`CancellationToken`), so a refusal anywhere earlier counts, and nothing later is faked.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.middlewares import auth as auth_module
from app.api.middlewares.auth import _normalize_regular_payload, authMiddleware
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.middlewares.identified_caller import (
    UNIDENTIFIED_CALLER_DETAIL,
    refuse_unidentified_caller,
    require_identified_caller,
)
from app.api.middlewares.principal_kind import oauth_principal_kind
from app.api.routes import chatbot
from tests.support.agent_routes import (
    USERS_COLL,
    InMemoryGraph,
    _TestAuth,
    as_user,
    make_client,
)

FIXTURE = (
    Path(__file__).resolve().parents[5]
    / "nodejs/apps/tests/modules/oauth_provider/fixtures/minted-token-shapes.json"
)
ORG = "org-1"
IDS = {
    "<ORG>": ORG,
    "<SERVICE_ACCOUNT>": "u-svc",
    "<PERSON>": "u-bob",
    "<ADMIN>": "u-alice",
    "<APP_CLIENT>": "client-123",
}
SCOPE = "conversation:chat agent:execute semantic:write"
_log = logging.getLogger(__name__)


def _fill(value: Any) -> Any:  # noqa: ANN401 - a JSON value
    if isinstance(value, str):
        for placeholder, real in IDS.items():
            value = value.replace(placeholder, real)
        return value
    if isinstance(value, dict):
        return {k: _fill(v) for k, v in value.items()}
    return value


def _cases(section: str) -> list[dict[str, Any]]:
    return [
        {**case, "claims": _fill(case["claims"])}
        for case in json.loads(FIXTURE.read_text())[section]
    ]


MINTED = {case["name"]: case for case in _cases("minted")}
NOT_MINTED = {case["name"]: case for case in _cases("notMinted")}


def _principal(name: str) -> dict[str, Any]:
    """`request.state.user` as the auth layer builds it for that minted shape."""
    claims = {**MINTED[name]["claims"], "orgId": ORG, "scope": SCOPE}
    return _normalize_regular_payload(claims, "tok")


SERVICE_TOKEN = "service account token"
DELEGATED = "delegated token"
PAT = "personal access token"


# --- the classification -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", [*MINTED.values(), *NOT_MINTED.values()], ids=lambda c: c["name"]
)
def test_every_token_shape_gets_the_kind_both_services_agree_on(
    case: dict[str, Any],
) -> None:
    assert oauth_principal_kind(case["claims"]) == case["kind"]


@pytest.mark.parametrize("case", MINTED.values(), ids=lambda c: c["name"])
def test_the_auth_layer_marks_each_minted_shape_with_its_kind(
    case: dict[str, Any],
) -> None:
    principal = _normalize_regular_payload(
        {**case["claims"], "orgId": ORG, "scope": SCOPE}, "tok"
    )
    assert principal["principalKind"] == case["kind"]


def test_a_service_account_token_keeps_its_own_identity() -> None:
    # Search and the other non-agent routes keep answering as the account: only the kind changes.
    principal = _principal(SERVICE_TOKEN)
    assert principal["userId"] == "u-svc"
    assert "appId" not in principal


def test_a_delegated_token_is_the_person_it_acts_as() -> None:
    principal = _principal(DELEGATED)
    assert (principal["principalKind"], principal["userId"]) == ("human", "u-bob")


@pytest.mark.parametrize(
    "case",
    [c for c in NOT_MINTED.values() if c["kind"] == "unknown"],
    ids=lambda c: c["name"],
)
def test_a_shape_no_minting_path_produces_is_refused_at_authentication(
    case: dict[str, Any],
) -> None:
    with pytest.raises(HTTPException) as exc:
        _normalize_regular_payload(
            {**case["claims"], "orgId": ORG, "scope": SCOPE}, "tok"
        )
    assert exc.value.status_code == 401


@pytest.mark.parametrize("kind", ["service_account", "unknown", "something-new"])
def test_the_guard_refuses_every_kind_that_is_not_a_person(kind: str) -> None:
    with pytest.raises(HTTPException) as exc:
        refuse_unidentified_caller(
            {"userId": "u-bob", "orgId": ORG, "principalKind": kind}
        )
    assert exc.value.detail == UNIDENTIFIED_CALLER_DETAIL


# --- every agent-loop entry, driven with each shape ---------------------------------------------------


class _LoopStarted(Exception):
    pass


class LoopProbe:
    """Stands in for `CancellationToken`, the first thing each loop entry creates after its own check."""

    def __init__(self) -> None:
        self.started = 0

    def __call__(self) -> None:
        self.started += 1
        raise _LoopStarted


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> LoopProbe:
    probe = LoopProbe()
    for module in (
        "app.agents.agent_loop.stream_bridge",
    ):
        monkeypatch.setattr(f"{module}.CancellationToken", probe)
    return probe


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def llm(*_a: object, **_k: object) -> tuple[object, dict, dict]:
        return (
            MagicMock(),
            {"provider": "openai", "isMultimodal": False, "contextLength": 128000},
            {},
        )

    monkeypatch.setattr("app.api.routes.agent.get_llm_for_chat", llm)
    monkeypatch.setattr("app.api.routes.chatbot.get_llm_for_chat", llm)
    monkeypatch.setattr(
        "app.utils.execute_query.has_sql_connector_configured",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.utils.fetch_slack_thread.has_slack_connector_configured",
        AsyncMock(return_value=False),
    )


SHAPES = [SERVICE_TOKEN, DELEGATED, PAT]


def _users() -> dict[str, dict[str, Any]]:
    return {name: _principal(name) for name in SHAPES}


def _agent_client() -> TestClient:
    graph = InMemoryGraph()
    graph.add_agent("plain", "alice", share_with_org=True)
    graph.add_agent(
        "service-agent", "alice", share_with_org=True, isServiceAccount=True
    )
    # The service account's own record, as the user sync writes it.
    graph.add_node(
        USERS_COLL,
        {
            "_key": "k-svc",
            "userId": "u-svc",
            "orgId": ORG,
            "email": "svc@service.test",
            "kind": "service",
        },
    )
    return make_client(graph, extra_users=_users())[0]


def _assistant_client() -> TestClient:
    app = FastAPI()
    registry = AsyncMock()
    registry.is_active = AsyncMock(return_value=False)
    config = AsyncMock()
    config.get_config.return_value = {"providers": []}
    app.container = MagicMock()
    app.container.logger.return_value = _log
    app.dependency_overrides.update(
        {
            chatbot.get_retrieval_service: lambda: AsyncMock(),
            chatbot.get_graph_provider: lambda: AsyncMock(),
            chatbot.get_config_service: lambda: config,
            chatbot.get_run_cancellation_registry: lambda: registry,
        }
    )
    app.include_router(chatbot.router, prefix="/api/v1")
    app.add_middleware(_TestAuth, users=_users())
    return TestClient(app, raise_server_exceptions=False)


ENTRIES = {
    "assistant /chat": ("assistant", "/api/v1/chat"),
    "assistant /chat/stream": ("assistant", "/api/v1/chat/stream"),
    "universal agent chat": ("agent", "/api/v1/agent/agentIdPlaceholder/chat"),
    "universal agent chat/stream": (
        "agent",
        "/api/v1/agent/agentIdPlaceholder/chat/stream",
    ),
    "named agent chat": ("agent", "/api/v1/agent/plain/chat"),
    "named agent chat/stream": ("agent", "/api/v1/agent/plain/chat/stream"),
    "service agent chat/stream": ("agent", "/api/v1/agent/service-agent/chat/stream"),
}


def _post(entry: str, shape: str):  # noqa: ANN202 - httpx.Response
    app, path = ENTRIES[entry]
    client = _assistant_client() if app == "assistant" else _agent_client()
    return client.post(path, headers=as_user(shape), json={"query": "hi"})


@pytest.mark.usefixtures("model")
@pytest.mark.parametrize("entry", ENTRIES)
def test_a_service_account_token_never_reaches_the_agent_loop(
    probe: LoopProbe, entry: str
) -> None:
    response = _post(entry, SERVICE_TOKEN)

    assert probe.started == 0
    assert response.status_code == 403
    assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL


@pytest.mark.usefixtures("model")
@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("shape", [DELEGATED, PAT])
def test_a_token_that_names_a_person_reaches_the_loop_as_that_person(
    probe: LoopProbe, entry: str, shape: str
) -> None:
    # Control: the same harness gets as far as the loop when a person is behind the token.
    _post(entry, shape)
    assert probe.started == 1


# --- the real header-to-decision path -----------------------------------------------------------------

JWT_SECRET = "session-secret-for-tests"


class _ConfigService:
    async def get_config(self, key: str, **_: Any) -> dict[str, str]:  # noqa: ANN401
        return {"jwtSecret": JWT_SECRET, "scopedJwtSecret": "scoped-secret-for-tests"}


@pytest.fixture
def guarded() -> TestClient:
    app = FastAPI()
    app.container = SimpleNamespace(logger=lambda: _log, config_service=_ConfigService)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):  # noqa: ANN202
        try:
            await authMiddleware(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
        return await call_next(request)

    @app.post("/run", dependencies=[Depends(require_identified_caller)])
    async def run(request: Request) -> dict[str, Any]:
        return {"userId": request.state.user["userId"]}

    auth_module._session_checks.clear()
    with patch(
        "app.api.middlewares.auth.fetch_caller_role",
        new_callable=AsyncMock,
        return_value=CallerRole(CallerRoleStatus.VALID, "member"),
    ):
        yield TestClient(app)
    auth_module._session_checks.clear()


def _signed(claims: dict[str, Any]) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iat": now,
            "exp": now + 300,
            "orgId": ORG,
            "scope": SCOPE,
            "iss": "http://node",
            **claims,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_a_signed_service_account_token_is_refused_by_the_guard(
    guarded: TestClient,
) -> None:
    response = guarded.post("/run", headers=_signed(MINTED[SERVICE_TOKEN]["claims"]))
    assert response.status_code == 403
    assert response.json()["detail"] == UNIDENTIFIED_CALLER_DETAIL


def test_a_signed_delegated_token_runs_as_the_person(guarded: TestClient) -> None:
    response = guarded.post("/run", headers=_signed(MINTED[DELEGATED]["claims"]))
    assert response.status_code == 200
    assert response.json() == {"userId": "u-bob"}


def test_a_signed_token_with_a_malformed_act_claim_is_not_authenticated(
    guarded: TestClient,
) -> None:
    response = guarded.post(
        "/run", headers=_signed(NOT_MINTED["act without a subject"]["claims"])
    )
    assert response.status_code == 401
