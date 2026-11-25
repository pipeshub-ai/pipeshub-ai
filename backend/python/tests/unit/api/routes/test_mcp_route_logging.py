"""MCP routes hand their failures to ``http.request.end`` (docs/logging.md, "Tool calls and MCP (Python)").

The real ``mcp_servers.router`` behind ``install_request_logging`` and an auth middleware shaped like the real one.
Each test asserts every WARNING+ record from ``app.*``, so a second line for one failure fails it, and the status and
body the client got.
"""

import ast
import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from mcp.shared.exceptions import MCPError

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_oauth_state_claim_path,
    get_mcp_oauth_state_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import dcr as dcr_module
from app.agents.mcp.dcr import DiscoveredOAuthMetadata
from app.agents.mcp.errors import (
    MCPHttpStatusError,
    MCPRequestNotSentError,
)
from app.agents.mcp.models import MCPAuthMode, OAuthTokens
from app.agents.mcp.oauth_client import (
    MCPOAuthError,
    MCPRefreshTokenInvalidError,
    exchange_code_for_token,
)
from app.api.middlewares.request_log import install_request_logging
from app.api.routes import mcp_servers
from app.utils.logger import log_record_fields
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed
from tests.support.bound_org import bound_like_auth
from tests.support.refresh_log_fakes import (
    SEEDED_RT,
    SEEDED_SECRET,
    TOKEN_URL,
    assert_no_secrets,
)
from tests.unit.agents.mcp.test_dcr import (
    _allow_all_discovery_targets,
    _mock_async_client,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

ORG = "65f0a1b2c3d4e5f6a7b8c9d0"
USER = "65f0a1b2c3d4e5f6a7b8c9d1"
OTHER_USER = "65f0a1b2c3d4e5f6a7b8c9ff"
EVENT = "http.request.end"
BASE = "/api/v1/mcp-servers"
# Built at run time: a traceback prints source lines, so a literal there would show in the stack.
SENTINEL = "-".join(("SENTINEL", "server-text")) + " alice@" + "example.com"


def _app(config_service: FakeConfigService) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = {"userId": USER, "orgId": ORG}
        with bound_like_auth(request.state.user):
            return await call_next(request)

    install_request_logging(app)
    app.include_router(mcp_servers.router)
    app.state.config_service = config_service
    app.state.mcp_registry = MagicMock()
    return app


@pytest.fixture(autouse=True)
def mcp_enabled() -> Iterator[None]:
    with patch("app.agents.mcp.service.is_mcp_enabled", new=AsyncMock(return_value=True)):
        yield


@pytest.fixture(autouse=True)
def capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="app.api.middlewares.request_log")
    return caplog


async def _call(app: FastAPI, method: str, path: str, **kwargs: object) -> httpx.Response:
    """One request in its own task, as uvicorn runs each."""
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return await asyncio.create_task(call())


def _loud(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.") and r.levelno >= logging.WARNING]


def _end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    (record,) = [r for r in caplog.records if r.getMessage() == EVENT]
    return record, log_record_fields(record)


def _all_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {log_record_fields(r)} {r.exc_text or ''}" for r in caplog.records)


def _instance(instance_id: str = "inst-1", **overrides: object) -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": ORG, "createdBy": USER, "name": instance_id, "typeId": None,
        "transport": "streamable_http", "url": "https://mcp.example.com/mcp", "authMode": MCPAuthMode.NONE.value,
        "isCustom": True, **overrides,
    }


def _server_down() -> MCPRequestNotSentError:
    """As the client raises it for a refused connection: the SDK's MCPError inside its task group."""
    error = MCPRequestNotSentError(f"Couldn't reach the MCP server: {SENTINEL}")
    error.__cause__ = ExceptionGroup("unhandled errors in a TaskGroup", [MCPError(-32000, SENTINEL)])
    return error


@contextmanager
def _tools_route(discover: AsyncMock) -> Iterator[None]:
    with ExitStack() as stack:
        for name, value in {
            "_get_org_instance": AsyncMock(return_value=_instance()),
            "_resolve_effective_user_auth": AsyncMock(return_value={}),
            "resolve_mcp_instances_with_inheritance": AsyncMock(return_value=[_instance()]),
            "discover_tools_for_owner": discover,
        }.items():
            stack.enter_context(patch.object(mcp_servers, name, new=value))
        yield


# --- test connection (tools refresh) --------------------------------------------------------------------------------


async def test_a_dead_server_on_test_connection_is_one_warning_without_its_text(capture) -> None:
    error = _server_down()
    assert SENTINEL in str(error)  # control: the text is there to leak

    with _tools_route(AsyncMock(side_effect=error)):
        response = await _call(_app(FakeConfigService()), "GET", f"{BASE}/instances/inst-1/tools")

    assert (response.status_code, response.json()) == (502, {"detail": action_failed("load this MCP server's tools")})
    record, fields = _end(capture)
    assert _loud(capture) == [record] and record.levelno == logging.WARNING
    assert (fields["status"], fields["error_class"], fields["reason"], fields["mcp_server_id"]) == (
        502, "unavailable", "unreachable", "inst-1")
    assert (fields["error_type"], fields["cause_type"]) == ("HTTPException", "MCPError")
    assert "error_message" not in fields and not record.exc_text
    assert SENTINEL not in _all_text(capture) and "alice@" not in _all_text(capture)

    capture.clear()  # a server that only wants a sign-in: 409, INFO
    with _tools_route(AsyncMock(side_effect=MCPHttpStatusError(401, SENTINEL))):
        response = await _call(_app(FakeConfigService()), "GET", f"{BASE}/instances/inst-1/tools")

    assert response.status_code == 409
    record, fields = _end(capture)
    assert _loud(capture) == [] and record.levelno == logging.INFO
    assert (fields["error_class"], fields["reason"]) == ("auth_expired", "unauthorized")
    assert SENTINEL not in _all_text(capture)


# --- OAuth refresh ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("error", "status", "level", "cls", "reason"), [
    (lambda: MCPRefreshTokenInvalidError(SENTINEL, error_code="invalid_grant", status=400), 409, logging.INFO,
     "auth_expired", "auth_expired"),
    # What the token client raises for a rejected client: not a rejected grant, so 502, ERROR (misconfigured).
    (lambda: MCPOAuthError(SENTINEL, error_code="invalid_client", status=401), 502, logging.ERROR,
     "misconfigured", "auth_expired"),
    # A rejected client with an overloaded endpoint's status is not a refusal for good: 502.
    (lambda: MCPOAuthError(SENTINEL, error_code="invalid_client", status=429), 502, logging.ERROR, "misconfigured",
     "unreachable"),
    (lambda: MCPOAuthError(SENTINEL, error_code="temporarily_unavailable", status=503), 502, logging.WARNING,
     "unavailable", "unreachable"),
], ids=["invalid-grant", "invalid-client", "invalid-client-transient", "provider-503"])
async def test_an_oauth_refresh_failure_is_one_line_at_its_class(capture, error, status, level, cls, reason) -> None:
    with (
        patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=_instance())),
        patch.object(mcp_servers.mcp_token_refresh, "refresh_credential_record", new=AsyncMock(side_effect=error())),
    ):
        store = FakeConfigService({get_mcp_credentials_path("inst-1", USER): {"isAuthenticated": True}})
        response = await _call(_app(store), "POST", f"{BASE}/instances/inst-1/oauth/refresh")

    assert response.status_code == status
    record, fields = _end(capture)
    assert record.levelno == level
    # The route signs nobody out itself (the shared refresh counts strikes): the request's line is the only one.
    assert _loud(capture) == ([record] if level >= logging.WARNING else [])
    assert (fields["error_class"], fields["reason"], fields["mcp_server_id"]) == (cls, reason, "inst-1")
    assert "error_message" not in fields
    # ERROR keeps the stack's frames, each exception's text omitted.
    assert ("<message omitted>" in (record.exc_text or "")) is (level >= logging.ERROR)
    assert SENTINEL not in _all_text(capture)


class _SaveFails(FakeConfigService):
    """`result`: what the write returns (None: it raises). `gone`: the credential is removed after the refresh read it."""

    def __init__(self, data: dict[str, Any], result: bool | None, *, gone: bool = False) -> None:
        super().__init__(data)
        self.result = result
        self.reads_left = 1 if gone else None

    async def get_config(self, key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if self.reads_left is not None and key == get_mcp_credentials_path("inst-1", USER):
            if self.reads_left == 0:
                return None
            self.reads_left -= 1
        return await super().get_config(key, *args, **kwargs)

    async def set_config(self, key: str, value: Any, **_: object) -> bool:  # noqa: ANN401
        if self.result is None:
            raise ConnectionError("the store is read-only")
        return self.result


@pytest.mark.parametrize(("saved", "gone", "strike"), [
    (True, False, {"strike": "1/3", "counted": True}),
    (False, False, {"strike": "1/3", "counted": False, "reason": "not_saved"}),
    (True, True, None),  # removed (Disconnect) before the strike's write: nothing to count
    (None, False, {"strike": "1/3", "counted": False}),  # the write raises: a failure line, still the provider's 409
], ids=["counted", "not-saved", "gone", "write-raises"])
async def test_a_rejected_refresh_through_the_real_route_is_one_end_and_its_strike(capture, saved, gone, strike) -> None:
    store = _SaveFails({
        get_mcp_credentials_path("inst-1", USER): {"isAuthenticated": True, "oauthTokens": {
            "accessToken": "at-old", "refreshToken": SEEDED_RT, "tokenUrl": f"{TOKEN_URL}?tenant={SEEDED_RT}",
        }},
        get_mcp_shared_dcr_client_path("inst-1"): {"clientId": "client-1", "clientSecret": SEEDED_SECRET},
    }, saved, gone=gone)
    rejected = httpx.Response(400, json={"error": "invalid_grant", "error_description": f"refresh token {SEEDED_RT} is not active"})
    # The real token client over a scripted transport (patching httpx.AsyncClient would patch this test's client too).
    endpoint = httpx.MockTransport(lambda _request: rejected)
    with (
        patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=_instance())),
        patch("app.agents.mcp.oauth_client.GuardedTransport", return_value=endpoint),
    ):
        response = await _call(_app(store), "POST", f"{BASE}/instances/inst-1/oauth/refresh")

    assert response.status_code == 409
    [end] = [r for r in capture.records if r.getMessage() == "credential_refresh.end"]
    assert end.levelno == logging.WARNING
    assert log_record_fields(end).items() >= {"error_class": "auth_expired", "reason": "invalid_grant"}.items()
    strikes = [r for r in capture.records if r.getMessage() == "credential_refresh.strike"]
    assert [{k: v for k, v in log_record_fields(r).items() if k in ("strike", "counted", "reason")} for r in strikes] == (
        [strike] if strike else [])
    for line in strikes:
        assert log_record_fields(line).items() >= {"trigger": "on_demand", "mcp_server_id": "inst-1", "owner_id": USER}.items()
    assert not [r for r in capture.records if r.getMessage().startswith("credential_refresh.deauth")]
    request_end, request_fields = _end(capture)
    assert request_fields["status"] == 409
    assert "error_message" not in request_fields
    # The write's own error stays off the provider's answer: nothing of it in any record.
    assert_no_secrets(capture)


# --- dynamic client registration -------------------------------------------------------------------------------------


DISCOVERED = DiscoveredOAuthMetadata(
    authorization_endpoint="https://auth.example.com/authorize", token_endpoint="https://auth.example.com/token",
    registration_endpoint="https://auth.example.com/register", code_challenge_methods_supported=["S256"],
)


def _registration_answer(status: int, body: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = f"{body}"
    resp.json.return_value = body
    inner = MagicMock()
    inner.post = AsyncMock(return_value=resp)
    return inner


def _registration_refused() -> MagicMock:
    inner = MagicMock()
    inner.post = AsyncMock(side_effect=httpx.ConnectError(SENTINEL))
    return inner


@pytest.mark.parametrize(("inner", "level", "cls"), [
    (lambda: _registration_answer(400, {"error": SENTINEL}), logging.ERROR, "misconfigured"),
    (lambda: _registration_answer(503, {"error": SENTINEL}), logging.WARNING, "unavailable"),
    (lambda: _registration_answer(500, {"error": SENTINEL}), logging.WARNING, "upstream_5xx"),
    (lambda: _registration_answer(201, {"note": SENTINEL}), logging.ERROR, "misconfigured"),  # no client_id
    (_registration_refused, logging.WARNING, "unavailable"),
], ids=["provider-400", "provider-503", "provider-500", "no-client-id", "connect-error"])
async def test_a_failed_registration_is_misconfigured_unless_the_provider_is_down(capture, inner, level, cls) -> None:
    instance = _instance(authMode=MCPAuthMode.OAUTH.value, scopes=["read"])
    app = _app(FakeConfigService())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    # Created before dcr's httpx.AsyncClient is patched: that patch is global to the httpx module.
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        with (
            patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=instance)),
            patch.object(mcp_servers, "_get_configured_frontend_base_url",
                         new=AsyncMock(return_value="https://app.example.com")),
            patch.object(dcr_module, "discover_oauth_metadata", new=AsyncMock(return_value=DISCOVERED)),
            _allow_all_discovery_targets(),
            _mock_async_client(inner()),
        ):
            response = await client.get(f"{BASE}/instances/inst-1/oauth/authorize")

    assert response.status_code == 502
    assert response.json()["detail"].startswith("Dynamic client registration with the OAuth provider failed.")
    record, fields = _end(capture)
    assert _loud(capture) == [record] and record.levelno == level
    assert (fields["error_class"], fields["reason"], fields["mcp_server_id"]) == (cls, "dcr_failed", "inst-1")
    assert "error_message" not in fields
    assert SENTINEL not in _all_text(capture)


# --- the listing -----------------------------------------------------------------------------------------------------


LISTED = ["mcp-ok", "mcp-down", "mcp-slow", "mcp-signin", "mcp-bug"]


async def _discover_by_id(instance: dict[str, Any], *_args: object, **_kwargs: object) -> tuple[list, float]:
    failures: dict[str, BaseException] = {
        "mcp-down": _server_down(),
        "mcp-slow": asyncio.TimeoutError(),
        "mcp-signin": MCPHttpStatusError(401, SENTINEL),
        "mcp-bug": KeyError(SENTINEL),
    }
    if instance["_id"] in failures:
        raise failures[instance["_id"]]
    return [], 0.0


@contextmanager
def _listing(ids: list[str]) -> Iterator[None]:
    with ExitStack() as stack:
        for name, value in {
            "resolve_mcp_instances_with_inheritance": AsyncMock(return_value=[_instance(i) for i in ids]),
            "_check_user_is_admin": AsyncMock(return_value=False),
            "resolve_instance_owner_config_service": AsyncMock(return_value=None),
            "_resolve_effective_user_auth": AsyncMock(return_value={}),
            "cached_tools_for_owner": AsyncMock(return_value=None),
            "discover_tools_for_owner": AsyncMock(side_effect=_discover_by_id),
        }.items():
            stack.enter_context(patch.object(mcp_servers, name, new=value))
        yield


def _discover_lines(caplog: pytest.LogCaptureFixture) -> list[tuple[int, dict[str, object]]]:
    return [(r.levelno, log_record_fields(r)) for r in caplog.records if r.getMessage() == "mcp.discover.end"]


async def test_a_listing_writes_one_discover_line_per_server_it_could_not_list(capture, monkeypatch) -> None:
    with _listing(["mcp-ok", "mcp-ok-2"]):  # control: all healthy
        response = await _call(_app(FakeConfigService()), "GET", f"{BASE}/my-mcp-servers?includeTools=true")
    assert response.status_code == 200 and _discover_lines(capture) == [] and _loud(capture) == []

    capture.clear()
    with _listing(LISTED):
        response = await _call(_app(FakeConfigService()), "GET", f"{BASE}/my-mcp-servers?includeTools=true")

    assert response.status_code == 200
    codes = {e["_id"]: e["toolsErrorCode"] for e in response.json()["instances"]}
    assert codes == {"mcp-ok": None, "mcp-down": "unreachable", "mcp-slow": "timeout", "mcp-signin": "unauthorized",
                     "mcp-bug": "error"}
    lines = {f["mcp_server_id"]: (level, f["error_class"], f["mcp_reason"], f["source"])
             for level, f in _discover_lines(capture)}
    assert lines == {
        "mcp-down": (logging.WARNING, "unavailable", "unreachable", "listing"),
        "mcp-slow": (logging.WARNING, "timeout", "timeout", "listing"),
        "mcp-signin": (logging.INFO, "auth_expired", "unauthorized", "listing"),
        "mcp-bug": (logging.ERROR, "internal", "error", "listing"),
    }
    (bug,) = [r for r in capture.records if getattr(r, "mcp_server_id", None) == "mcp-bug"]
    assert "KeyError" in (bug.exc_text or "")  # our bug: the stack, once
    end, fields = _end(capture)
    assert (end.levelno, fields["outcome"], fields["status"]) == (logging.INFO, "success", 200)
    assert len(_loud(capture)) == 3 and end not in _loud(capture)
    assert SENTINEL not in _all_text(capture)

    capture.clear()  # a listing out of time: the server is skipped, not failed
    monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_DEADLINE_SECONDS", 0.0)
    with _listing(["mcp-ok"]):
        await _call(_app(FakeConfigService()), "GET", f"{BASE}/my-mcp-servers?includeTools=true")
    ((level, skipped),) = _discover_lines(capture)
    assert (level, skipped["outcome"], skipped["reason"], skipped["mcp_server_id"]) == (
        logging.WARNING, "skipped", "listing_deadline", "mcp-ok")


# --- a store or graph outage (pattern A) ----------------------------------------------------------------------------


async def test_an_mcp_delete_during_a_graph_outage_is_one_warning_with_the_id(capture) -> None:
    graph = MagicMock()
    graph.check_mcp_instance_in_use = AsyncMock(side_effect=ConnectionError("graph down"))
    with (
        patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=_instance())),
        patch.object(mcp_servers, "_require_manage_access", new=AsyncMock()),
        patch.object(mcp_servers, "forbid_inherited_mcp_mutation"),
        patch("app.api.routes.toolsets._get_graph_provider", return_value=graph),
    ):
        response = await _call(_app(FakeConfigService()), "DELETE", f"{BASE}/instances/inst-1")

    assert (response.status_code, response.json()) == (500, {
        "detail": "Cannot delete the MCP server: unable to verify whether agents use it. Please try again."})
    record, fields = _end(capture)
    assert _loud(capture) == [record] and record.levelno == logging.WARNING
    assert (fields["error_class"], fields["mcp_server_id"], fields["cause_type"]) == (
        "unavailable", "inst-1", "ConnectionError")
    assert not [r for r in capture.records if r.name == mcp_servers.logger.name]


# --- the OAuth callback ----------------------------------------------------------------------------------------------


class _Flow:
    """One MCP sign-in's callback against an in-memory store; each case breaks one step."""

    def __init__(self) -> None:
        self.store = _SavingStore()
        self.instance: dict[str, Any] | None = _instance(authMode=MCPAuthMode.OAUTH.value)
        self.secret = AsyncMock(return_value=None)
        self.exchange = AsyncMock(return_value=OAuthTokens(access_token="at", refresh_token="rt"))
        self.edit_access = AsyncMock(return_value={})

    def start(self, state: str, **overrides: object) -> str:
        self.store.data[get_mcp_oauth_state_path(state)] = {
            "instanceId": "inst-1", "orgId": ORG, "initiatedBy": USER, "userId": USER, "ownerType": "user",
            "clientId": "cid", "tokenUrl": "https://auth.example.com/token", "redirectUri": "https://app/cb",
            "expiresAt": get_epoch_timestamp_in_ms() + 600_000, **overrides,
        }
        return state

    @contextmanager
    def patched(self) -> Iterator[None]:
        refresh = MagicMock()
        refresh.get_mcp_token_refresh_service.return_value = None
        with (
            patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(side_effect=lambda *_a, **_k: self.instance)),
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=self.secret),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=self.exchange),
            patch.object(mcp_servers, "_require_mcp_agent_edit_access", new=self.edit_access),
            patch.object(mcp_servers.step_up, "clear_step_up_scopes", new=AsyncMock()),
            patch("app.connectors.core.base.token_service.startup_service.startup_service", refresh),
        ):
            yield


class _SavingStore(FakeConfigService):
    def __init__(self) -> None:
        super().__init__()
        self.saves = True

    async def set_config(self, key: str, value: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        return self.saves and await super().set_config(key, value, **kwargs)


def _provider_error(flow: _Flow) -> str:
    return "error=access_denied&state=s"


def _provider_error_unknown(flow: _Flow) -> str:
    return f"error={SENTINEL}&state=s"


def _missing_params(flow: _Flow) -> str:
    return "state=s"


def _unknown_state(flow: _Flow) -> str:
    return "code=c&state=never-started"


def _expired_state(flow: _Flow) -> str:
    return f"code=c&state={flow.start('s', expiresAt=1)}"


def _caller_mismatch(flow: _Flow) -> str:
    return f"code=c&state={flow.start('s', initiatedBy=OTHER_USER)}"


def _claimed_state(flow: _Flow) -> str:
    flow.store.data[get_mcp_oauth_state_claim_path("s")] = {"claimedBy": USER}
    return f"code=c&state={flow.start('s')}"


def _agent_access_revoked(flow: _Flow) -> str:
    flow.edit_access.side_effect = HTTPException(403, "no edit access")
    return f"code=c&state={flow.start('s', ownerType='agent', userId='agent-1')}"


def _instance_gone(flow: _Flow) -> str:
    flow.instance = None
    return f"code=c&state={flow.start('s')}"


def _client_changed(flow: _Flow) -> str:
    flow.secret.side_effect = ValueError(SENTINEL)
    return f"code=c&state={flow.start('s')}"


def _exchange_refused(flow: _Flow) -> str:
    flow.exchange.side_effect = MCPOAuthError(SENTINEL, error_code="invalid_grant", status=400)
    return f"code=c&state={flow.start('s')}"


def _exchange_answered_200(flow: _Flow) -> str:
    # GitHub's sign-in answer for a code that expired or was used twice: the user's, not our configuration (F26 N1).
    flow.exchange.side_effect = MCPOAuthError(SENTINEL, error_code="bad_verification_code", status=200)
    return f"code=c&state={flow.start('s')}"


def _not_saved(flow: _Flow) -> str:
    flow.store.saves = False
    return f"code=c&state={flow.start('s')}"


@pytest.mark.parametrize(("break_step", "body_error", "level", "cls", "error_type", "ids"), [
    (_provider_error, "access_denied", logging.INFO, "cancelled", None, {}),
    (_provider_error_unknown, SENTINEL, logging.WARNING, "forbidden", None, {}),
    (_missing_params, "missing_params", logging.WARNING, "bad_request", None, {}),
    (_unknown_state, "invalid_state", logging.WARNING, "bad_request", None, {}),
    (_expired_state, "expired_state", logging.WARNING, "bad_request", None, {}),
    (_caller_mismatch, "caller_mismatch", logging.WARNING, "forbidden", None, {"mcp_server_id": "inst-1"}),
    (_claimed_state, "invalid_state", logging.WARNING, "bad_request", None, {"mcp_server_id": "inst-1"}),
    (_agent_access_revoked, "agent_access_revoked", logging.WARNING, "forbidden", "HTTPException",
     {"mcp_server_id": "inst-1", "agent_id": "agent-1"}),
    (_instance_gone, "instance_not_found", logging.WARNING, "not_found", None, {"mcp_server_id": "inst-1"}),
    (_client_changed, "client_config_changed", logging.WARNING, "conflict", "ValueError", {"mcp_server_id": "inst-1"}),
    (_exchange_refused, "token_exchange_failed", logging.WARNING, "auth_expired", "MCPOAuthError",
     {"mcp_server_id": "inst-1"}),
    (_exchange_answered_200, "token_exchange_failed", logging.WARNING, "auth_expired", "MCPOAuthError",
     {"mcp_server_id": "inst-1"}),
    (_not_saved, "credentials_not_saved", logging.WARNING, "unavailable", None, {"mcp_server_id": "inst-1"}),
])
async def test_each_callback_failure_is_one_end_line_with_its_reason(capture, break_step, body_error, level, cls,
                                                                     error_type, ids) -> None:
    flow = _Flow()
    with flow.patched():
        # Control, same case: a sign-in that completes is a success line.
        ok = await _call(_app(flow.store), "GET", f"{BASE}/oauth/callback?code=c&state={flow.start('ok')}")
        assert ok.json() == {"success": True, "instanceId": "inst-1"}
        record, fields = _end(capture)
        assert (record.levelno, fields["outcome"], fields["mcp_server_id"]) == (logging.INFO, "success", "inst-1")

        capture.clear()
        response = await _call(_app(flow.store), "GET", f"{BASE}/oauth/callback?{break_step(flow)}")

    assert response.status_code == 200
    assert (response.json()["success"], response.json()["error"]) == (False, body_error)
    record, fields = _end(capture)
    assert record.levelno == level and _loud(capture) == ([record] if level >= logging.WARNING else [])
    reason = "provider_error" if break_step in (_provider_error, _provider_error_unknown) else body_error
    assert (fields["status"], fields["outcome"], fields["error_class"], fields["reason"]) == (
        200, "failure", cls, reason)
    assert fields.get("error_type") == error_type and "error_message" not in fields
    assert {k: fields[k] for k in ("mcp_server_id", "agent_id") if k in fields} == ids
    if break_step in (_provider_error, _provider_error_unknown):
        assert fields["oauth_error"] == ("access_denied" if break_step is _provider_error else "other")
    text = _all_text(capture)
    assert SENTINEL not in text and OTHER_USER not in text


# --- no caught exception's text in a log call ------------------------------------------------------------------------


ROUTES = Path(mcp_servers.__file__).parent
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _logged_exceptions(source: str) -> list[int]:
    """Lines of ``logger.<level>(…)`` calls inside ``except … as e`` that pass ``e`` (in an f-string or as an arg)."""
    return [
        call.lineno
        for handler in ast.walk(ast.parse(source)) if isinstance(handler, ast.ExceptHandler) and handler.name
        for call in ast.walk(handler)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS
        and "logger" in ast.unparse(call.func.value)
        and any(isinstance(n, ast.Name) and n.id == handler.name
                for arg in [*call.args, *(k.value for k in call.keywords)] for n in ast.walk(arg))
    ]


def test_no_route_log_call_carries_a_caught_exception() -> None:
    sample = "try:\n    pass\nexcept Exception as e:\n    logger.error(f'failed: {e}')\n"
    assert _logged_exceptions(sample) == [4]  # control: the check sees one

    for name in ("mcp_servers.py", "toolsets.py", "api_toolsets.py"):
        assert _logged_exceptions((ROUTES / name).read_text()) == [], name


# --- a token endpoint that never answers -----------------------------------------------------------------------------
# The real token client and refresh down to `oauth_client._post_token_request`, through a real httpx client whose
# transport fails the way httpx does. The provider is down: every surface says so, as for a 503, and keeps the sign-in.

NO_ANSWER = [
    pytest.param(lambda: httpx.ConnectError(SENTINEL), "unavailable", id="refused"),
    pytest.param(lambda: httpx.ConnectTimeout(SENTINEL), "timeout", id="connect-timeout"),
    pytest.param(lambda: httpx.ReadTimeout(SENTINEL), "timeout", id="read-timeout"),
    pytest.param(lambda: httpx.RemoteProtocolError(SENTINEL), "unavailable", id="cut-off"),
]
CRED_PATH = f"/services/mcp/credentials/inst-1/{USER}"


@contextmanager
def _token_endpoint_failing(raised: Exception) -> Iterator[None]:
    def fail(request: httpx.Request) -> httpx.Response:
        raise raised

    with patch("app.agents.mcp.oauth_client.GuardedTransport", new=lambda *_a, **_k: httpx.MockTransport(fail)):
        yield


def _signed_in() -> tuple[FakeConfigService, dict[str, Any]]:
    auth = {"isAuthenticated": True, "oauthTokens": {
        "accessToken": "old", "refreshToken": "r", "tokenUrl": "https://auth.example.com/token"}}
    return FakeConfigService({CRED_PATH: auth, "/services/mcp/dcr-clients/inst-1": {"clientId": "client"}}), auth


def _rejecting_the_access_token() -> AsyncMock:
    """The MCP server answers 401, so discovery refreshes the sign-in once."""
    return AsyncMock(side_effect=MCPHttpStatusError(401, "expired"))


def _assert_quiet_lines(caplog: pytest.LogCaptureFixture, record: logging.LogRecord, cls: str, *, refreshes: int) -> None:
    """The request's line, after the refresh attempt's own `credential_refresh.end` when one ran: each a WARNING
    of the cell's class, no stack. The attempt line carries the transport error's text by design; no other line may."""
    attempts = [r for r in caplog.records if r.getMessage() == "credential_refresh.end"]
    assert len(attempts) == refreshes
    for attempt in attempts:
        fields = log_record_fields(attempt)
        assert (attempt.levelno, fields["outcome"], fields["error_class"]) == (logging.WARNING, "failure", cls)
        assert not attempt.exc_text
    assert record.levelno == logging.WARNING and _loud(caplog) == [*attempts, record]
    assert not record.exc_text and SENTINEL not in _text_outside_attempts(caplog)


def _text_outside_attempts(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {log_record_fields(r)} {r.exc_text or ''}"
                     for r in caplog.records if r.getMessage() != "credential_refresh.end")


@pytest.mark.parametrize(("raised", "cls"), NO_ANSWER)
async def test_a_callback_whose_token_endpoint_never_answers_says_the_exchange_failed(capture, raised, cls) -> None:
    flow = _Flow()
    flow.exchange = exchange_code_for_token  # the real one
    with flow.patched(), _token_endpoint_failing(raised()):
        response = await _call(_app(flow.store), "GET", f"{BASE}/oauth/callback?code=c&state={flow.start('s')}")

    assert response.status_code == 200
    assert response.json() == {
        "success": False, "error": "token_exchange_failed",
        "errorMessage": "Failed to exchange the authorization code for tokens. Please try connecting again.",
    }
    record, fields = _end(capture)
    _assert_quiet_lines(capture, record, cls, refreshes=0)
    assert (fields["error_class"], fields["reason"], fields["mcp_server_id"]) == (cls, "token_exchange_failed", "inst-1")


@pytest.mark.parametrize(("raised", "cls"), NO_ANSWER)
async def test_a_refresh_whose_token_endpoint_never_answers_is_a_bad_gateway(capture, raised, cls) -> None:
    store, _ = _signed_in()
    with patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=_instance())), \
         _token_endpoint_failing(raised()):
        response = await _call(_app(store), "POST", f"{BASE}/instances/inst-1/oauth/refresh")

    assert (response.status_code, response.json()) == (
        502, {"detail": "Failed to refresh the OAuth token. Please try again or reconnect this MCP server."})
    record, fields = _end(capture)
    _assert_quiet_lines(capture, record, cls, refreshes=1)
    assert (fields["error_class"], fields["reason"], fields["mcp_server_id"]) == (cls, "unreachable", "inst-1")
    assert store.data[CRED_PATH]["isAuthenticated"] is True  # an outage signs nobody out


@pytest.mark.parametrize(("raised", "cls"), NO_ANSWER)
async def test_test_connection_whose_sign_in_cannot_be_renewed_is_a_bad_gateway(capture, raised, cls) -> None:
    store, auth = _signed_in()
    instance = _instance(authMode=MCPAuthMode.OAUTH.value)
    with patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=instance)), \
         patch.object(mcp_servers, "_resolve_effective_user_auth", new=AsyncMock(return_value=auth)), \
         patch.object(mcp_servers, "resolve_mcp_instances_with_inheritance", new=AsyncMock(return_value=[instance])), \
         patch("app.agents.mcp.discovery.discover_tool_listing", new=_rejecting_the_access_token()), \
         _token_endpoint_failing(raised()):
        response = await _call(_app(store), "GET", f"{BASE}/instances/inst-1/tools")

    assert (response.status_code, response.json()) == (502, {"detail": action_failed("load this MCP server's tools")})
    record, fields = _end(capture)
    _assert_quiet_lines(capture, record, cls, refreshes=1)
    assert (fields["error_class"], fields["reason"]) == (cls, "unreachable")


@pytest.mark.parametrize(("raised", "cls"), NO_ANSWER)
async def test_a_listed_server_whose_sign_in_cannot_be_renewed_is_unreachable(capture, raised, cls) -> None:
    store, auth = _signed_in()
    instance = _instance(authMode=MCPAuthMode.OAUTH.value)
    with ExitStack() as stack:
        for name, value in {
            "resolve_mcp_instances_with_inheritance": AsyncMock(return_value=[instance]),
            "_check_user_is_admin": AsyncMock(return_value=False),
            "resolve_instance_owner_config_service": AsyncMock(return_value=None),
            "_resolve_effective_user_auth": AsyncMock(return_value=auth),
            "cached_tools_for_owner": AsyncMock(return_value=None),
        }.items():
            stack.enter_context(patch.object(mcp_servers, name, new=value))
        stack.enter_context(patch("app.agents.mcp.discovery.discover_tool_listing", new=_rejecting_the_access_token()))
        stack.enter_context(_token_endpoint_failing(raised()))
        response = await _call(_app(store), "GET", f"{BASE}/my-mcp-servers?includeTools=true")

    assert response.status_code == 200
    (entry,) = response.json()["instances"]
    # Red "can't reach", not the grey "slow to respond" a timeout code shows as a still-connected server.
    assert (entry["toolsErrorCode"], entry["toolsError"]) == ("unreachable", mcp_servers._SIGN_IN_RENEWAL_UNAVAILABLE)
    assert not entry.get("toolsTimedOut")
    ((level, fields),) = _discover_lines(capture)
    assert (level, fields["error_class"], fields["mcp_reason"], fields["source"]) == (
        logging.WARNING, cls, "unreachable", "listing")
    (attempt,) = [r for r in capture.records if r.getMessage() == "credential_refresh.end"]
    assert (attempt.levelno, log_record_fields(attempt)["error_class"]) == (logging.WARNING, cls)
    assert not any(r.exc_text for r in capture.records) and SENTINEL not in _text_outside_attempts(capture)
    assert store.data[CRED_PATH]["isAuthenticated"] is True


# --- a token answer with a malformed field (F26e) ---------------------------------------------------------------------
# The real token client parses the provider's 200: a field of the wrong shape is the provider's or the OAuth app's
# setup, so it is misconfigured, never quoted, and each route answers the way it answers any token-endpoint failure.

MALFORMED = {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": f"soon-{SENTINEL}"}


@contextmanager
def _token_endpoint_answering(body: dict[str, Any]) -> Iterator[None]:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=body))
    with patch("app.agents.mcp.oauth_client.GuardedTransport", new=lambda *_a, **_k: transport):
        yield


async def test_a_callback_whose_provider_answers_a_malformed_token_says_so_and_saves_nothing(capture) -> None:
    flow = _Flow()
    flow.exchange = exchange_code_for_token  # the real one
    with flow.patched(), _token_endpoint_answering(MALFORMED):
        response = await _call(_app(flow.store), "GET", f"{BASE}/oauth/callback?code=c&state={flow.start('s')}")

    assert response.status_code == 200
    assert response.json() == {
        "success": False, "error": "token_exchange_failed",
        "errorMessage": (
            "The sign-in provider sent an invalid token, so connecting again won't help. "
            "Ask your administrator to check this server's OAuth app with the provider."
        ),
    }
    _, fields = _end(capture)
    assert (fields["error_class"], fields["reason"], fields["error_type"]) == (
        "misconfigured", "token_exchange_failed", "MCPMalformedTokenError")
    assert not [key for key in flow.store.data if key.startswith("/services/mcp/credentials/")]
    assert SENTINEL not in _all_text(capture)


async def test_a_refresh_whose_provider_answers_a_malformed_token_is_a_bad_gateway(capture) -> None:
    store, auth = _signed_in()
    saved = dict(auth["oauthTokens"])
    with patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=_instance())), \
         _token_endpoint_answering(MALFORMED):
        response = await _call(_app(store), "POST", f"{BASE}/instances/inst-1/oauth/refresh")

    assert (response.status_code, response.json()) == (
        502, {"detail": "Failed to refresh the OAuth token. Please try again or reconnect this MCP server."})
    attempt = next(r for r in capture.records if r.getMessage() == "credential_refresh.end")
    assert (attempt.levelno, log_record_fields(attempt)["error_class"], log_record_fields(attempt)["reason"]) == (
        logging.ERROR, "misconfigured", "malformed_token")
    _, fields = _end(capture)
    assert fields["error_class"] == "misconfigured"
    assert store.data[CRED_PATH]["oauthTokens"] == saved
    assert SENTINEL not in _all_text(capture)
