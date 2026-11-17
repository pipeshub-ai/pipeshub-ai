"""What one MCP token refresh logs, whoever starts it (docs/logging.md, "Credential refresh").

`app.agents.mcp.token_refresh.refresh_credential_record` is the choke point every MCP refresh goes through. The
real token client runs against a scripted endpoint (tests/support/mcp_oauth_fakes.py); the guard tests run the
real guarded transport. Records are captured from every logger at DEBUG, and every failure test checks that no
seeded secret, the token URL's host or the provider's prose reaches any record.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_refresh_lock_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import token_refresh, url_guard
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.failure import (
    MCPFailureReason,
    classify_mcp_failure,
    log_discover_end,
    mcp_error_class,
)
from app.agents.mcp.oauth_client import MCPRefreshTokenInvalidError
from app.core import org_context as oc
from app.utils import credential_refresh_log as CR
from app.utils import logging_fields
from app.utils import request_context as rc
from app.utils.error_class import log_failure
from app.utils.logger import JsonFormatter
from app.utils.structured_logging import DefaultLoggingProvider
from app.utils.url_fetcher import FetchError
from tests.support.mcp_oauth_fakes import json_response, mock_async_client
from tests.support.refresh_log_fakes import (
    SEEDED_RT,
    SEEDED_SECRET,
    TOKEN_URL,
    assert_no_secrets,
    events,
    fields,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

INST = "inst-1"
OWNER = "65f0a1b2c3d4e5f6a7b8c9d1"
ORG = "65f0a1b2c3d4e5f6a7b8c9d0"
CRED = get_mcp_credentials_path(INST, OWNER)
SHARED = get_mcp_shared_dcr_client_path(INST)
LEGACY = get_mcp_dcr_client_path(INST, OWNER)
# The tenant query carries a secret: a token URL in any record would leak it.
TOKEN_URL_Q = f"{TOKEN_URL}?tenant={SEEDED_RT}"
START, END = "credential_refresh.start", "credential_refresh.end"
RETIRED = "Removing the OAuth client the provider rejected"
SAVE_FAILED = "Saving the refreshed credentials failed"
OK = (200, {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600})


def _body(error: str) -> dict[str, str]:
    return {"error": error, "error_description": f"refresh token {SEEDED_RT} is not active"}


def _record(**tokens: object) -> dict[str, Any]:
    oauth = {"accessToken": "at-old", "refreshToken": SEEDED_RT, "tokenUrl": TOKEN_URL_Q, "expiresIn": 3600, **tokens}
    return {"isAuthenticated": True, "orgId": ORG, "oauthTokens": {k: v for k, v in oauth.items() if v is not None}}


def _store(record: object = None, **data: object) -> FakeConfigService:
    seeded: dict[str, object] = {SHARED: {"clientId": "client-1", "clientSecret": SEEDED_SECRET}, **data}
    if record is not False:
        seeded[CRED] = record if record is not None else _record()
    return FakeConfigService(seeded)


class Endpoint:
    """The token endpoint: each POST takes the next answer, (status, body) or an exception; `before` runs first."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.before: Callable[[], Any] | None = None

    async def post(self, *_: object, **__: object) -> MagicMock:
        self.calls += 1
        if self.before is not None:
            await self.before()
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer
        return json_response(status, body)


@pytest.fixture(autouse=True)
def _fresh_gate_and_raw_ids(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setattr(logging_fields, "_log_once_last", {})
    monkeypatch.setattr(rc, "_service_suffix", "")
    monkeypatch.setattr(url_guard, "_env_proxy_for", lambda _url: None)
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    ep = Endpoint()
    inner = MagicMock()
    inner.post = AsyncMock(side_effect=ep.post)
    with mock_async_client(inner):
        yield ep


def _as_user() -> Any:  # noqa: ANN401
    return oc.bind(oc.OrgContext(ORG, OWNER, "user", "jwt", "req-1"))


async def _refresh(store: FakeConfigService, **kwargs: object) -> Any:  # noqa: ANN401
    with _as_user():
        return await token_refresh.refresh_credential_record(INST, OWNER, store, **kwargs)


async def _refresh_failing(store: FakeConfigService) -> BaseException:
    with pytest.raises(Exception) as exc_info:
        await _refresh(store)
    return exc_info.value


def _age_the_gate() -> None:
    for key in logging_fields._log_once_last:
        logging_fields._log_once_last[key] -= CR.MISCONFIGURED_REPEAT_S + 1


def _loud(caplog: pytest.LogCaptureFixture, level: int = logging.WARNING) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= level]


def _the_end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    [end] = events(caplog, END)
    return end, fields(end)


class TestSuccess:
    async def test_on_demand_is_one_info_end_naming_the_sign_in_and_its_org(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [OK]
        store = _store()

        await _refresh(store)

        end, f = _the_end(caplog)
        assert end.levelno == logging.INFO
        assert f.items() >= {
            "outcome": "success", "refresh_kind": "mcp", "trigger": "on_demand", "mcp_server_id": INST,
            "owner_id": OWNER, "org_id": ORG, "user_id": OWNER,
        }.items()
        assert isinstance(f["duration_ms"], int)
        assert "reason" not in f
        assert [r.levelno for r in events(caplog, START)] == [logging.DEBUG]
        assert [r.getMessage() for r in caplog.records if r.name.startswith("app.") and r.levelno >= logging.INFO] == [END]
        assert store.data[CRED]["oauthTokens"]["refreshToken"] == "rt-new"

    async def test_tokens_another_process_already_refreshed_are_adopted(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _refresh(_store(), stale_access_token="at-stale")

        assert _the_end(caplog)[1].items() >= {"outcome": "success", "reason": "adopted"}.items()
        assert endpoint.calls == 0

    async def test_a_refresh_token_replaced_during_the_request_is_superseded(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        store = _store()

        async def sign_in_again() -> None:
            store.data[CRED] = _record(refreshToken="rt-from-a-new-sign-in")

        endpoint.answers, endpoint.before = [OK], sign_in_again
        await _refresh(store)

        assert _the_end(caplog)[1].items() >= {"outcome": "success", "reason": "superseded"}.items()

    async def test_inside_a_scan_scope_the_end_says_scan(self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture) -> None:
        endpoint.answers = [OK]
        with token_refresh.credential_scope(INST, OWNER, trigger=CR.Trigger.SCAN, org_id=ORG):
            await token_refresh.refresh_credential_record(INST, OWNER, _store())

        assert _the_end(caplog)[1].items() >= {"trigger": "scan", "org_id": ORG, "ctx_kind": "job"}.items()

    async def test_a_save_that_fails_twice_warns_per_try_then_errors_without_the_path(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [OK]
        store = _store()
        store.set_config = AsyncMock(return_value=False)  # type: ignore[method-assign]

        await _refresh(store)

        tries = [r for r in caplog.records if r.getMessage() == SAVE_FAILED]
        assert [(r.levelno, fields(r)["attempt"], fields(r)["mcp_server_id"]) for r in tries] == [
            (logging.WARNING, "1/2", INST), (logging.WARNING, "2/2", INST),
        ]
        assert all("error_type" not in fields(r) for r in tries)
        [error] = _loud(caplog, logging.ERROR)
        assert "could not be saved" in error.getMessage()
        assert CRED not in error.getMessage()
        assert fields(error)["mcp_server_id"] == INST
        assert _the_end(caplog)[1]["outcome"] == "success"
        assert_no_secrets(caplog)

    async def test_a_save_that_raises_warns_with_the_error_type(self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture) -> None:
        endpoint.answers = [OK]
        store = _store()
        store.set_config = AsyncMock(side_effect=ConnectionError(f"store down at {CRED}"))  # type: ignore[method-assign]

        await _refresh(store)

        tries = [r for r in caplog.records if r.getMessage() == SAVE_FAILED]
        assert [fields(r)["error_type"] for r in tries] == ["ConnectionError", "ConnectionError"]
        assert all(CRED not in r.getMessage() for r in caplog.records)


class TestProviderAnswers:
    @pytest.mark.parametrize("error", ["invalid_grant", "invalid_request"])
    async def test_a_rejected_grant_is_one_auth_expired_warning_without_the_text(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, error: str,
    ) -> None:
        endpoint.answers = [(400, _body(error))]

        exc = await _refresh_failing(_store())

        assert isinstance(exc, MCPRefreshTokenInvalidError)
        end, f = _the_end(caplog)
        assert (end.levelno, end.exc_info, end.exc_text) == (logging.WARNING, None, None)
        assert f.items() >= {
            "outcome": "failure", "error_class": "auth_expired", "reason": "invalid_grant", "http_status": 400,
            "oauth_error": error, "mcp_server_id": INST, "owner_id": OWNER, "org_id": ORG,
        }.items()
        assert "error_message" not in f
        assert_no_secrets(caplog)

    @pytest.mark.parametrize(("status", "cls"), [(503, "unavailable"), (429, "rate_limited"), (502, "upstream_5xx")])
    async def test_an_outage_is_one_warning_by_class(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, status: int, cls: str,
    ) -> None:
        endpoint.answers = [(status, {"detail": f"busy, retry the refresh of {SEEDED_RT}"})]

        await _refresh_failing(_store())

        end, f = _the_end(caplog)
        assert (end.levelno, end.exc_text) == (logging.WARNING, None)
        assert (f["error_class"], f["http_status"]) == (cls, status)
        assert _loud(caplog) == [end]
        assert_no_secrets(caplog)

    @pytest.mark.parametrize(("error", "cls"), [
        (httpx.ConnectError(f"connection refused by {TOKEN_URL_Q}"), "unavailable"),
        (httpx.ReadTimeout(f"timed out reading {TOKEN_URL_Q}"), "timeout"),
    ])
    async def test_a_request_that_fails_is_one_warning_without_a_stack(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, error: Exception, cls: str,
    ) -> None:
        endpoint.answers = [error]

        await _refresh_failing(_store())

        end, f = _the_end(caplog)
        assert (end.levelno, end.exc_text, f["error_class"]) == (logging.WARNING, None, cls)
        assert not [r for r in caplog.records if "Traceback" in r.getMessage()]
        assert_no_secrets(caplog)

    async def test_a_rejected_shared_client_is_one_error_an_hour_and_one_retire_warning(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [(401, _body("invalid_client"))] * 3
        store = _store()

        await _refresh_failing(store)
        await _refresh_failing(store)

        first, second = events(caplog, END)
        assert (first.levelno, second.levelno) == (logging.ERROR, logging.DEBUG)
        assert fields(first).items() >= {"error_class": "misconfigured", "http_status": 401, "oauth_error": "invalid_client"}.items()
        assert "<message omitted>" in first.exc_text
        assert first.exc_info is None
        [retired] = [r for r in caplog.records if r.getMessage() == RETIRED]
        assert retired.levelno == logging.WARNING
        assert fields(retired).items() >= {"mcp_server_id": INST, "oauth_client": "client-1", "client_kind": "shared"}.items()
        assert store.data[SHARED].get(token_refresh.REJECTED_AT_FIELD)
        assert_no_secrets(caplog)

        # Positive control: once the hour is up it is an ERROR again.
        _age_the_gate()
        await _refresh_failing(store)
        assert events(caplog, END)[-1].levelno == logging.ERROR

    @pytest.mark.parametrize(("data", "issued_by", "kind"), [
        ({LEGACY: {"clientId": "client-legacy", "clientSecret": SEEDED_SECRET}}, None, "legacy"),
        ({SHARED: {"clientId": "client-2", token_refresh.PREVIOUS_CLIENT_FIELD: {"clientId": "client-1"}}}, "client-1",
         "previous"),
    ])
    async def test_each_retired_client_kind_is_named(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, data: dict, issued_by: str | None, kind: str,
    ) -> None:
        endpoint.answers = [(401, _body("invalid_client"))]
        store = _store(_record(clientId=issued_by), **data)

        await _refresh_failing(store)

        [retired] = [r for r in caplog.records if r.getMessage() == RETIRED]
        assert fields(retired)["client_kind"] == kind
        if kind == "legacy":
            assert LEGACY not in store.data
        else:
            assert token_refresh.PREVIOUS_CLIENT_FIELD not in store.data[SHARED]
        assert_no_secrets(caplog)

    async def test_a_store_error_while_retiring_does_not_carry_the_providers_answer(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        class DeleteFails(FakeConfigService):
            async def delete_config(self, key: str) -> bool:
                if key == LEGACY:
                    raise RuntimeError("the store is read-only")
                return await super().delete_config(key)

        endpoint.answers = [(401, _body("invalid_client"))]
        store = DeleteFails({**_store().data, LEGACY: {"clientId": "client-legacy"}, CRED: _record()})

        exc = await _refresh_failing(store)

        assert isinstance(exc, RuntimeError)
        assert (exc.__cause__, exc.__context__) == (None, None)
        # What a request's own line (P2's http.request.end) writes for whatever escapes, with its text.
        log_failure(logging.getLogger("app.api.middlewares.request_log"), "http.request.end", exc, include_error_text=True)
        assert_no_secrets(caplog)


class TestTheTokenUrlGuard:
    async def test_a_refused_token_url_is_misconfigured_once_an_hour(self, caplog: pytest.LogCaptureFixture) -> None:
        store = _store(_record(tokenUrl=f"http://127.0.0.1:9/token?tenant={SEEDED_RT}"))

        await _refresh_failing(store)
        await _refresh_failing(store)

        first, second = events(caplog, END)
        assert (first.levelno, second.levelno) == (logging.ERROR, logging.DEBUG)
        assert fields(first).items() >= {"error_class": "misconfigured", "reason": "url_blocked"}.items()
        assert "http_status" not in fields(first)  # no answer came: FetchError's stand-in 0 is not a status
        assert first.exc_text
        assert_no_secrets(caplog)

    async def test_a_token_host_that_does_not_resolve_is_unavailable_never_gated(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def no_such_host(*_: object, **__: object) -> list:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", no_such_host)
        store = _store()

        exc = await _refresh_failing(store)
        await _refresh_failing(store)

        ends = events(caplog, END)
        assert [r.levelno for r in ends] == [logging.WARNING, logging.WARNING]
        assert all(r.exc_text is None for r in ends)
        assert fields(ends[0]).items() >= {"error_class": "unavailable", "reason": "host_unresolved"}.items()
        assert "http_status" not in fields(ends[0])
        # The tool call's or discovery's line of a turn says the same class, at its level.
        assert mcp_error_class(exc) == "unavailable"
        log_discover_end(logging.getLogger("app.agents.agent_loop.mcp_tool_loader"), INST, exc, source="turn")
        [discover] = [r for r in caplog.records if r.getMessage() == "mcp.discover.end"]
        assert (discover.levelno, fields(discover)["error_class"]) == (logging.WARNING, "unavailable")
        # The host is ours to name (the guard's line names it); the query and the secrets are not.
        rendered = "".join(JsonFormatter().format(r) for r in caplog.records)
        assert SEEDED_RT not in rendered and SEEDED_SECRET not in rendered and "tenant=" not in rendered


    async def test_a_dns_failure_deeper_in_the_chain_is_still_host_unresolved(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The guard re-wrapped once more: the DNS error is two causes down, not the blocked error's own cause.
        async def refused(*_: object, **__: object) -> None:
            try:
                try:
                    raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
                except socket.gaierror as dns:
                    raise FetchError("lookup failed") from dns
            except FetchError as e:
                raise MCPUrlBlockedError("The MCP server URL is not allowed.") from e

        monkeypatch.setattr(url_guard, "assert_mcp_url_allowed", refused)

        await _refresh_failing(_store())

        end, f = _the_end(caplog)
        assert (end.levelno, f["error_class"], f["reason"]) == (logging.WARNING, "unavailable", "host_unresolved")


class TestLocalFailures:
    async def test_a_cluster_lock_held_elsewhere_is_one_start_and_one_end(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(token_refresh, "CLUSTER_LOCK_WAIT_SECONDS", 0)
        store = _store(**{get_mcp_refresh_lock_path(INST, OWNER): {"owner": "another-replica"}})

        await _refresh_failing(store)

        assert len(events(caplog, START)) == 1
        end, f = _the_end(caplog)
        assert (end.levelno, f["error_class"], f["reason"]) == (logging.WARNING, "unavailable", "lock_contention")
        assert endpoint.calls == 0
        assert_no_secrets(caplog)

    @pytest.mark.parametrize(("case", "cls", "reason"), [
        ("no record", "not_found", "no_credential"),
        ("tokens not a dict", "auth_expired", "invalid_token_record"),
        ("no refresh token", "auth_expired", "no_refresh_token"),
        ("no token url", "auth_expired", "no_token_url"),
        ("no client", "misconfigured", "config_missing"),
        ("removed mid-refresh", "conflict", "credential_removed"),
    ])
    async def test_each_record_problem_has_its_reason_and_class(
        self, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, case: str, cls: str, reason: str,
    ) -> None:
        store = {
            "no record": lambda: _store(False),
            "tokens not a dict": lambda: _store({"isAuthenticated": True, "oauthTokens": "not-a-dict"}),
            "no refresh token": lambda: _store(_record(refreshToken=None)),
            "no token url": lambda: _store(_record(tokenUrl=None)),
            "no client": lambda: FakeConfigService({CRED: _record()}),
            "removed mid-refresh": _store,
        }[case]()
        if case == "removed mid-refresh":
            async def disconnect() -> None:
                del store.data[CRED]

            endpoint.answers, endpoint.before = [OK], disconnect

        exc = await _refresh_failing(store)

        end, f = _the_end(caplog)
        assert (f["error_class"], f["reason"]) == (cls, reason)
        assert end.levelno == (logging.ERROR if cls == "misconfigured" else logging.WARNING)
        # Guard: the same exception as before for callers, and the same UI reason.
        assert isinstance(exc, ValueError)
        assert classify_mcp_failure(exc) is MCPFailureReason.AUTH_EXPIRED
        assert_no_secrets(caplog)

    async def test_a_missing_client_is_one_error_an_hour(self, caplog: pytest.LogCaptureFixture) -> None:
        store = FakeConfigService({CRED: _record()})

        await _refresh_failing(store)
        await _refresh_failing(store)

        assert [r.levelno for r in events(caplog, END)] == [logging.ERROR, logging.DEBUG]


class TestACallerThatLeaves:
    @pytest.fixture
    def spawned(self, monkeypatch: pytest.MonkeyPatch) -> list[asyncio.Future]:
        """The tasks token_refresh starts, so the test can wait for the shielded refresh."""
        tasks: list[asyncio.Future] = []

        class Spy:
            def __getattr__(self, name: str) -> object:
                return getattr(asyncio, name)

            @staticmethod
            def ensure_future(coro: object) -> asyncio.Future:
                task = asyncio.ensure_future(coro)  # type: ignore[arg-type]
                tasks.append(task)
                return task

        monkeypatch.setattr(token_refresh, "asyncio", Spy())
        return tasks

    async def _leave_then_answer(self, endpoint: Endpoint, spawned: list[asyncio.Future], answer: tuple) -> asyncio.Future:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate() -> None:
            entered.set()
            await release.wait()

        endpoint.answers, endpoint.before = [answer], gate
        caller = asyncio.ensure_future(_refresh(_store()))
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        [refresh] = spawned
        await asyncio.wait({refresh})
        return refresh

    async def test_its_refresh_still_ends_with_the_ids_and_org(
        self, endpoint: Endpoint, spawned: list[asyncio.Future], caplog: pytest.LogCaptureFixture,
    ) -> None:
        refresh = await self._leave_then_answer(endpoint, spawned, OK)

        assert refresh.result().access_token == "at-new"
        assert _the_end(caplog)[1].items() >= {
            "outcome": "success", "mcp_server_id": INST, "owner_id": OWNER, "org_id": ORG,
        }.items()

    async def test_its_failed_refresh_is_one_end_and_the_exception_is_retrieved(
        self, endpoint: Endpoint, spawned: list[asyncio.Future], caplog: pytest.LogCaptureFixture,
    ) -> None:
        refresh = await self._leave_then_answer(endpoint, spawned, (503, {"detail": f"down for {SEEDED_RT}"}))

        assert refresh._log_traceback is False  # type: ignore[attr-defined] # no "exception was never retrieved"
        end, f = _the_end(caplog)
        assert (end.levelno, f["mcp_server_id"], f["owner_id"], f["org_id"]) == (logging.WARNING, INST, OWNER, ORG)
        assert_no_secrets(caplog)


def test_a_failed_end_renders_as_json_with_its_fields_and_no_traceback_in_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        await _refresh_failing(FakeConfigService({CRED: _record()}))

    asyncio.run(run())

    [end] = events(caplog, END)
    rendered = json.loads(JsonFormatter().format(end))
    assert rendered["message"] == END
    assert {k: rendered[k] for k in ("error_class", "reason", "refresh_kind", "mcp_server_id")} == {
        "error_class": "misconfigured", "reason": "config_missing", "refresh_kind": "mcp", "mcp_server_id": INST,
    }
    assert "Traceback" not in rendered["message"]


class TestTheTags:
    @pytest.mark.parametrize(("reason", "cls"), [
        ("no_refresh_token", "auth_expired"), ("invalid_token_record", "auth_expired"), ("no_token_url", "auth_expired"),
        ("no_credential", "not_found"), ("credential_removed", "conflict"), ("lock_contention", "unavailable"),
        ("url_blocked", "misconfigured"), ("host_unresolved", "unavailable"),
    ])
    def test_each_mcp_reason_has_its_class(self, reason: str, cls: str) -> None:
        assert CR.ERROR_CLASS_BY_REASON[reason] == cls
        assert CR.refresh_error_class(token_refresh.MCPTokenRefreshError("x", refresh_reason=reason)) == cls

    def test_an_untagged_exception_has_no_refresh_class(self) -> None:
        assert CR.refresh_error_class(token_refresh.MCPTokenRefreshError("no refresh token")) is None
        assert CR.refresh_error_class(ValueError("x")) is None

    def test_a_tag_is_read_through_the_chain_and_a_rejected_client_stays_misconfigured(self) -> None:
        try:
            try:
                raise token_refresh.MCPRefreshLockTimeoutError("busy")
            except token_refresh.MCPTokenRefreshError as e:
                raise RuntimeError("wrapped") from e
        except RuntimeError as wrapped:
            assert CR.refresh_error_class(wrapped) == "unavailable"
        rejected_client = MCPRefreshTokenInvalidError("x", error_code="invalid_client", status=401)
        assert CR.refresh_error_class(rejected_client) == "misconfigured"
