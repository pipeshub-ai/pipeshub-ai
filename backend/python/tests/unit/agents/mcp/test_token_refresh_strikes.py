"""One sign-out rule for every MCP refresh path (`app.agents.mcp.token_refresh._count_rejection`).

A refusal that says the sign-in can't be renewed (a dead grant, or the provider refusing our client) is a strike on the
credential record, whichever path saw it, at most one per `STRIKE_SPACING_MS`; the third signs it out with
`deauthReason`. The real choke point and token client answer a scripted token endpoint (`GuardedTransport` swapped for
`httpx.MockTransport`, so the route's own test client is untouched); the clock is the module's
`get_epoch_timestamp_in_ms`, patched.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_instance_path,
    get_mcp_refresh_lock_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import discovery, token_refresh
from app.agents.mcp.errors import MCPHttpStatusError, MCPUrlBlockedError
from app.agents.mcp.models import MCPAuthMode
from app.agents.mcp.oauth_client import (
    MCPOAuthError,
    MCPRefreshTokenInvalidError,
    refresh_access_token,
)
from app.api.routes import mcp_servers
from app.connectors.core.base.token_service import (
    mcp_token_refresh_service as svc_module,
)
from app.utils import logging_fields
from app.utils import request_context as rc
from app.utils.structured_logging import DefaultLoggingProvider
from tests.support.refresh_log_fakes import (
    SEEDED_RT,
    SEEDED_SECRET,
    TOKEN_URL,
    assert_no_secrets,
    events,
    fields,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_mcp_session import (
    _FakeClientManagerFactory,
    _server,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService
from tests.unit.api.routes.test_mcp_route_logging import USER, _app, _call, _instance

INST = "inst-1"
CRED = get_mcp_credentials_path(INST, USER)
SHARED = get_mcp_shared_dcr_client_path(INST)
T0 = 1_800_000_000_000
WINDOW = token_refresh.STRIKE_SPACING_MS
STRIKE, DEAUTH, DEAUTH_FAILED, END = (
    "credential_refresh.strike", "credential_refresh.deauthenticated", "credential_refresh.deauthenticate_failed",
    "credential_refresh.end",
)
COUNT, LAST = token_refresh.REJECTIONS_FIELD, token_refresh.LAST_REJECTED_AT_FIELD


def _refused(code: str, status: int = 400) -> tuple[int, dict]:
    # Providers echo the refresh token in prose: no line may carry it.
    return status, {"error": code, "error_description": f"refresh token {SEEDED_RT} is not active"}


INVALID_GRANT = _refused("invalid_grant")
OK = (200, {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600})


def _record(**extra: object) -> dict[str, Any]:
    created = datetime.now(timezone.utc) - timedelta(seconds=7200)  # due: the background refreshes it now
    return {"isAuthenticated": True, "orgId": "org-1", "oauthTokens": {
        "accessToken": "at-old", "refreshToken": SEEDED_RT, "tokenUrl": TOKEN_URL, "expiresIn": 3600,
        "createdAt": created.isoformat()}, **extra}


def _store(record: dict | None = None, owner: str = USER) -> FakeConfigService:
    return FakeConfigService({
        get_mcp_instance_path(INST): {"_id": INST},
        SHARED: {"clientId": "client-1", "clientSecret": SEEDED_SECRET},
        get_mcp_credentials_path(INST, owner): record if record is not None else _record(),
    })


Answer = tuple | BaseException | Callable[[], tuple]


@contextmanager
def _token_endpoint(*answers: Answer) -> Iterator[list[httpx.Request]]:
    """Each POST takes the next answer: (status, json), (status, text, content-type), an exception, or a callable
    run first (a side effect while the request is in flight) that returns one of those."""
    queue, seen = list(answers), []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        given = queue.pop(0)
        if callable(given) and not isinstance(given, BaseException):
            given = given()
        if isinstance(given, BaseException):
            raise given
        if len(given) == 3:
            return httpx.Response(given[0], text=given[1], headers={"content-type": given[2]})
        return httpx.Response(given[0], json=given[1])

    with patch("app.agents.mcp.oauth_client.GuardedTransport", new=lambda *_a, **_k: httpx.MockTransport(answer)):
        yield seen


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> int:
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> Clock:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setattr(logging_fields, "_log_once_last", {})
    monkeypatch.setattr(rc, "_service_suffix", "")
    caplog.set_level(logging.DEBUG)
    c = Clock()
    monkeypatch.setattr(token_refresh, "get_epoch_timestamp_in_ms", c)
    return c


async def _refresh(store: FakeConfigService, owner: str = USER, stale: str | None = "at-old") -> BaseException:
    with pytest.raises(Exception) as caught:
        await token_refresh.refresh_credential_record(INST, owner, store, stale_access_token=stale)
    return caught.value


def _strikes(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        {k: v for k, v in fields(r).items() if k in ("strike", "counted", "reason")} for r in events(caplog, STRIKE)
    ]


def _cred(store: FakeConfigService, owner: str = USER) -> dict[str, Any]:
    return store.data[get_mcp_credentials_path(INST, owner)]


def _spaced(clock: Clock, n: int) -> Iterator[int]:
    for i in range(n):
        clock.now = T0 + i * WINDOW
        yield i


class TestTheCount:
    async def test_one_refusal_is_one_strike_on_the_record(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store()
        with _token_endpoint(INVALID_GRANT):
            exc = await _refresh(store)

        assert type(exc) is MCPRefreshTokenInvalidError
        record = _cred(store)
        assert (record["isAuthenticated"], record[COUNT], record[LAST]) == (True, 1, T0)
        assert _strikes(caplog) == [{"strike": "1/3", "counted": True}]
        assert not events(caplog, DEAUTH)
        assert_no_secrets(caplog)

    async def test_the_third_spaced_refusal_signs_out_and_says_why(
        self, clock: Clock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        store = _store()
        with _token_endpoint(*[INVALID_GRANT] * 3):
            for _ in _spaced(clock, 3):
                assert type(await _refresh(store)) is MCPRefreshTokenInvalidError

        record = _cred(store)
        assert (record["isAuthenticated"], record["deauthReason"], record["updatedAt"]) == (False, "refresh_token_invalid", T0 + 2 * WINDOW)
        assert COUNT not in record and LAST not in record
        assert [s["strike"] for s in _strikes(caplog)] == ["1/3", "2/3", "3/3"]
        [deauth] = events(caplog, DEAUTH)
        assert deauth.levelno == logging.WARNING
        assert fields(deauth).items() >= {"reason": "refresh_token_invalid", "strikes": 3, "mcp_server_id": INST}.items()
        assert "path" not in fields(deauth)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert_no_secrets(caplog)

    async def test_a_signed_out_credential_is_not_struck_again(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store(_record(isAuthenticated=False))
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert _cred(store) == before
        assert not events(caplog, STRIKE)


class TestTheWindow:
    async def test_one_strike_per_window(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store()
        with _token_endpoint(*[INVALID_GRANT] * 3):
            await _refresh(store)
            clock.now = T0 + WINDOW - 1
            writes = len(store.writes)
            await _refresh(store)
            assert len(store.writes) == writes + 1  # only the cluster lock key, not the record
            assert store.writes[-1] != CRED
            clock.now = T0 + WINDOW
            await _refresh(store)

        assert (_cred(store)[COUNT], _cred(store)[LAST]) == (2, T0 + WINDOW)
        assert _strikes(caplog) == [
            {"strike": "1/3", "counted": True},
            {"strike": "1/3", "counted": False, "reason": "within_window"},
            {"strike": "2/3", "counted": True},
        ]

    async def test_a_clock_behind_the_last_strike_counts_as_inside(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store(_record(**{COUNT: 1, LAST: T0}))
        clock.now = T0 - 60_000
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert _cred(store)[COUNT] == 1
        assert _strikes(caplog)[-1]["reason"] == "within_window"

    async def test_a_run_with_no_strike_for_a_day_is_forgotten(self, clock: Clock) -> None:
        store = _store(_record(**{COUNT: 2, LAST: T0 - token_refresh.STRIKE_MEMORY_MS - 1}))
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert (_cred(store)["isAuthenticated"], _cred(store)[COUNT]) == (True, 1)

    async def test_a_strike_exactly_a_day_after_the_last_still_continues_the_run(self, clock: Clock) -> None:
        store = _store(_record(**{COUNT: 2, LAST: T0 - token_refresh.STRIKE_MEMORY_MS}))
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert (_cred(store)["isAuthenticated"], _cred(store)["deauthReason"]) == (False, "refresh_token_invalid")

    async def test_a_last_strike_from_a_clock_far_ahead_is_stale(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        # Otherwise every correct clock would read it as inside the window, and nothing would count again.
        store = _store(_record(**{COUNT: 2, LAST: T0 + token_refresh.STRIKE_MEMORY_MS}))
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert (_cred(store)["isAuthenticated"], _cred(store)[COUNT], _cred(store)[LAST]) == (True, 1, T0)
        assert _strikes(caplog) == [{"strike": "1/3", "counted": True}]

    async def test_a_last_strike_up_to_one_window_ahead_is_skew_inside_the_window(self, clock: Clock) -> None:
        store = _store(_record(**{COUNT: 1, LAST: T0 + WINDOW}))
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert _cred(store) == before

    @pytest.mark.parametrize(("count", "last"), [("x", T0 - WINDOW), (-1, T0 - WINDOW), (True, T0 - WINDOW), (2, "123")])
    async def test_corrupt_fields_count_from_zero(self, clock: Clock, count: object, last: object) -> None:
        store = _store(_record(**{COUNT: count, LAST: last}))
        with _token_endpoint(INVALID_GRANT):
            await _refresh(store)
        assert (_cred(store)["isAuthenticated"], _cred(store)[COUNT], _cred(store)[LAST]) == (True, 1, T0)


class TestASuccess:
    async def test_clears_the_count(self, clock: Clock) -> None:
        store = _store(_record(**{COUNT: 2, LAST: T0 - WINDOW}))
        with _token_endpoint(OK):
            await token_refresh.refresh_credential_record(INST, USER, store, stale_access_token="at-old")
        assert COUNT not in _cred(store) and LAST not in _cred(store)

    async def test_adopted_tokens_leave_the_count_alone(self, clock: Clock) -> None:
        store = _store(_record(**{COUNT: 1, LAST: T0}))
        with _token_endpoint() as seen:
            await token_refresh.refresh_credential_record(INST, USER, store, stale_access_token="at-stale")
        assert not seen
        assert (_cred(store)[COUNT], _cred(store)[LAST]) == (1, T0)


class _YieldingStore(FakeConfigService):
    """A store that yields on every read and write, as a real one does, so concurrent callers interleave unless a lock
    keeps them apart."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(data)
        self.lock_attempts = 0

    async def get_config(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(0)
        return await super().get_config(*args, **kwargs)

    async def set_config(self, *args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        await asyncio.sleep(0)
        return await super().set_config(*args, **kwargs)

    async def create_config_if_absent(self, key: str, *args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        self.lock_attempts += 1
        await asyncio.sleep(0)
        return await super().create_config_if_absent(key, *args, **kwargs)


class _ProcessLockPerCall(dict):
    """Each caller its own process lock, as on separate replicas: only the cluster lock is shared."""

    def __getitem__(self, key: object) -> asyncio.Lock:
        return asyncio.Lock()


class TestConcurrentCallersShareOneStrike:
    async def test_replicas_hearing_one_answer_count_one_strike_through_the_cluster_lock(
        self, clock: Clock, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(token_refresh, "_refresh_locks", _ProcessLockPerCall())
        monkeypatch.setattr(token_refresh, "_CLUSTER_LOCK_POLL_SECONDS", 0.001)
        store = _YieldingStore(_store().data)
        with _token_endpoint(*[INVALID_GRANT] * 3) as seen:
            await asyncio.gather(*(_refresh(store) for _ in range(3)))

        assert len(seen) == 3
        assert [s["counted"] for s in _strikes(caplog)] == [True, False, False]
        assert _cred(store)[COUNT] == 1

    async def test_callers_in_one_process_queue_on_its_lock_not_on_the_cluster_key(
        self, clock: Clock, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(token_refresh, "_CLUSTER_LOCK_POLL_SECONDS", 0.001)
        store = _YieldingStore(_store().data)
        with _token_endpoint(*[INVALID_GRANT] * 3):
            await asyncio.gather(*(_refresh(store) for _ in range(3)))

        # One cluster-key attempt per caller: without the process lock, the waiters poll the key while it is held.
        assert store.lock_attempts == 3
        assert [s["counted"] for s in _strikes(caplog)] == [True, False, False]

    async def test_three_tool_calls_refreshing_at_once(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store()
        with _token_endpoint(*[INVALID_GRANT] * 3) as seen:
            results = await asyncio.gather(*(_refresh(store) for _ in range(3)))

        assert len(seen) == 3 and all(type(r) is MCPRefreshTokenInvalidError for r in results)
        assert _cred(store)[COUNT] == 1
        assert [s.get("reason") for s in _strikes(caplog)] == [None, "within_window", "within_window"]

    async def test_every_replica_scanning_the_same_credential(self, clock: Clock) -> None:
        store = _store()
        replicas = [svc_module.MCPTokenRefreshService(store) for _ in range(3)]
        with _token_endpoint(*[INVALID_GRANT] * 3) as seen:
            await asyncio.gather(*(replica._refresh_all_tokens() for replica in replicas))
        assert len(seen) == 3
        assert (_cred(store)["isAuthenticated"], _cred(store)[COUNT]) == (True, 1)

    async def test_runs_of_a_service_account_agent_share_its_record(self, clock: Clock) -> None:
        agent_key = "agent-key-1"
        store = _store(owner=agent_key)
        with _token_endpoint(*[INVALID_GRANT] * 2):
            await asyncio.gather(_refresh(store, owner=agent_key), _refresh(store, owner=agent_key))
        assert _cred(store, agent_key)[COUNT] == 1


class TestWhatCounts:
    @pytest.mark.parametrize("answer", [
        _refused("invalid_client", 401), _refused("unauthorized_client"),
        (200, "error=incorrect_client_credentials", "application/x-www-form-urlencoded"),
    ], ids=["invalid_client", "unauthorized_client", "incorrect_client_credentials"])
    async def test_a_refused_client_is_a_misconfiguration_that_still_signs_out(
        self, clock: Clock, caplog: pytest.LogCaptureFixture, answer: tuple,
    ) -> None:
        store = _store()
        with _token_endpoint(*[answer] * 3):
            for _ in _spaced(clock, 3):
                exc = await _refresh(store)
                assert type(exc) is MCPOAuthError

        assert (_cred(store)["isAuthenticated"], _cred(store)["deauthReason"]) == (False, "oauth_client_rejected")
        assert fields(events(caplog, DEAUTH)[0])["reason"] == "oauth_client_rejected"
        assert {fields(r)["error_class"] for r in events(caplog, END)} == {"misconfigured"}
        # Only invalid_client retires the client; the others are left as they are.
        assert bool(store.data[SHARED].get(token_refresh.REJECTED_AT_FIELD)) is (answer[0] == 401)
        assert_no_secrets(caplog)

    async def test_a_client_code_wins_over_a_grant_text_marker(self) -> None:
        answer = (200, "error=incorrect_client_credentials&error_description=refresh+token+is+invalid",
                  "application/x-www-form-urlencoded")
        with _token_endpoint(answer), pytest.raises(MCPOAuthError) as caught:
            await refresh_access_token(TOKEN_URL, "client-1", "s", "rt", allow_private=False)
        assert type(caught.value) is MCPOAuthError
        assert caught.value.deauth_reason == "oauth_client_rejected"

    @pytest.mark.parametrize("code", ["interaction_required", "login_required", "consent_required",
                                      "bad_refresh_token", "invalid_refresh_token"])
    async def test_the_dead_grant_family_counts(self, clock: Clock, code: str) -> None:
        store = _store()
        with _token_endpoint(_refused(code)):
            assert (await _refresh(store)).deauth_reason == "refresh_token_invalid"
        assert _cred(store)[COUNT] == 1

    async def test_a_grant_text_marker_without_a_code_counts(self, clock: Clock) -> None:
        store = _store()
        with _token_endpoint((400, "the refresh token has expired", "text/html")):
            await _refresh(store)
        assert _cred(store)[COUNT] == 1

    @pytest.mark.parametrize("answer", [
        _refused("invalid_request"), _refused("invalid_scope"), _refused("unsupported_grant_type"),
        (400, {"error": "invalid_request", "error_description": "refresh token is invalid"}),
    ], ids=["invalid_request", "invalid_scope", "unsupported_grant_type", "request_code_over_text_marker"])
    async def test_a_refused_request_shape_is_no_shared_strike(
        self, clock: Clock, caplog: pytest.LogCaptureFixture, answer: tuple,
    ) -> None:
        store = _store()
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(answer):
            exc = await _refresh(store)
        assert type(exc) is MCPRefreshTokenInvalidError and exc.deauth_reason is None
        assert _cred(store) == before
        assert not events(caplog, STRIKE)

    @pytest.mark.parametrize("answer", [
        _refused("invalid_client", 503), _refused("invalid_grant", 429), httpx.ConnectError("refused"),
        (400, {}), (400, {"error": "something_else"}), MCPUrlBlockedError("blocked by policy"),
    ], ids=["client_503", "grant_429", "no_answer", "400_no_code", "400_unknown_code", "url_blocked"])
    async def test_what_a_retry_can_fix_is_no_strike(self, clock: Clock, answer: Answer) -> None:
        store = _store()
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(answer):
            exc = await _refresh(store)
        assert isinstance(exc, MCPOAuthError) and exc.deauth_reason is None
        assert _cred(store) == before

    async def test_a_host_that_does_not_resolve_is_no_strike(self, clock: Clock) -> None:
        store = _store()
        before = copy.deepcopy(_cred(store))

        def unresolved() -> BaseException:
            try:
                raise socket.gaierror("no such host")
            except socket.gaierror as cause:
                blocked = MCPUrlBlockedError("unresolvable")
                blocked.__cause__ = cause
                return blocked

        with _token_endpoint(unresolved):
            exc = await _refresh(store)
        assert exc.refresh_reason == "host_unresolved"
        assert _cred(store) == before

    async def test_refusals_that_never_reach_the_endpoint_are_no_strike(
        self, clock: Clock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _store(_record(oauthTokens={"accessToken": "at-old", "tokenUrl": TOKEN_URL}))
        assert isinstance(await _refresh(store), token_refresh.MCPTokenRefreshError)

        monkeypatch.setattr(token_refresh, "CLUSTER_LOCK_WAIT_SECONDS", 0)
        store = _store()
        store.data[get_mcp_refresh_lock_path(INST, USER)] = {"owner": "another-process"}
        before = copy.deepcopy(_cred(store))
        assert isinstance(await _refresh(store), token_refresh.MCPRefreshLockTimeoutError)
        assert _cred(store) == before


class TestTheRecordChangedDuringTheRequest:
    async def test_a_new_sign_in_is_not_struck(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store()
        fresh = _record(oauthTokens={"accessToken": "at-fresh", "refreshToken": "rt-fresh", "tokenUrl": TOKEN_URL})

        def sign_in_again() -> tuple:
            store.data[CRED] = copy.deepcopy(fresh)
            return INVALID_GRANT

        with _token_endpoint(sign_in_again):
            await _refresh(store)
        assert _cred(store) == fresh
        assert _strikes(caplog) == [{"strike": "0/3", "counted": False, "reason": "rotated"}]

    async def test_a_removed_credential_is_not_brought_back(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store()

        def disconnect() -> tuple:
            del store.data[CRED]
            return INVALID_GRANT

        with _token_endpoint(disconnect):
            assert type(await _refresh(store)) is MCPRefreshTokenInvalidError
        assert CRED not in store.data
        assert not events(caplog, STRIKE)


class _CredWriteFails(FakeConfigService):
    """The credential write fails (False, as `ConfigurationService.set_config` reports a store error, or raises)
    and stores nothing; every other key behaves."""

    def __init__(self, data: dict[str, Any], outcome: object) -> None:
        super().__init__(data)
        self.outcome = outcome

    async def set_config(self, key: str, value: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        if key != CRED:
            return await super().set_config(key, value, **kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return False


class TestAFailedWrite:
    """The stored record must equal a deep copy taken before the call: an in-place edit of the record
    `get_config` returned (the fake's live dict, the real service's cached one) fails these."""

    @pytest.mark.parametrize("outcome", [False, RuntimeError("store is read-only")], ids=["not_saved", "raises"])
    async def test_below_the_threshold_nothing_changes_and_no_strike_is_claimed(
        self, clock: Clock, caplog: pytest.LogCaptureFixture, outcome: object,
    ) -> None:
        store = _CredWriteFails(_store().data, outcome)
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(INVALID_GRANT):
            exc = await _refresh(store)

        assert type(exc) is MCPRefreshTokenInvalidError and exc.__context__ is None
        assert _cred(store) == before
        assert not [s for s in _strikes(caplog) if s["counted"] is True]
        [line] = events(caplog, STRIKE)
        assert fields(line)["counted"] is False
        if outcome is False:
            assert fields(line)["reason"] == "not_saved"
        assert_no_secrets(caplog)

    # A store outage classes below ERROR on its own: the sign-out that didn't happen is forced to ERROR.
    @pytest.mark.parametrize("outcome", [False, ConnectionError("store down")], ids=["not_saved", "raises"])
    async def test_at_the_threshold_the_sign_out_failure_is_an_error_and_the_next_answer_retries_it(
        self, clock: Clock, caplog: pytest.LogCaptureFixture, outcome: object,
    ) -> None:
        store = _CredWriteFails(_store(_record(**{COUNT: 2, LAST: T0 - WINDOW})).data, outcome)
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(INVALID_GRANT, INVALID_GRANT):
            exc = await _refresh(store)
            assert type(exc) is MCPRefreshTokenInvalidError and exc.__context__ is None
            assert _cred(store) == before
            [failed] = events(caplog, DEAUTH_FAILED)
            assert (failed.levelno, fields(failed)["strike"]) == (logging.ERROR, "3/3")
            assert not events(caplog, DEAUTH) and not events(caplog, STRIKE)

            store.outcome = None
            store.set_config = FakeConfigService.set_config.__get__(store)  # the store is back
            clock.now = T0 + 1
            await _refresh(store)
        assert (_cred(store)["isAuthenticated"], _cred(store)["deauthReason"]) == (False, "refresh_token_invalid")
        assert_no_secrets(caplog)

    @pytest.mark.parametrize("outcome", [False, RuntimeError("store is read-only")], ids=["not_saved", "raises"])
    async def test_a_success_whose_write_fails_changes_nothing(self, clock: Clock, outcome: object) -> None:
        store = _CredWriteFails(_store(_record(**{COUNT: 2, LAST: T0 - WINDOW})).data, outcome)
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(OK):
            tokens = await token_refresh.refresh_credential_record(INST, USER, store, stale_access_token="at-old")
        assert tokens.access_token == "at-new"  # still returned: they work for this request
        assert _cred(store) == before


class TestEveryPath:
    async def _route(self, store: FakeConfigService) -> int:
        with patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=_instance())):
            response = await _call(_app(store), "POST", f"/api/v1/mcp-servers/instances/{INST}/oauth/refresh")
        return response.status_code

    async def test_route(self, clock: Clock) -> None:
        store = _store()
        with _token_endpoint(*[INVALID_GRANT] * 3):
            for i in _spaced(clock, 3):
                assert await self._route(store) == 409
                assert _cred(store)["isAuthenticated"] is (i < 2)
        assert _cred(store)["deauthReason"] == "refresh_token_invalid"

    async def test_route_signs_nobody_out_for_a_refused_request_shape(self, clock: Clock) -> None:
        store = _store()
        before = copy.deepcopy(_cred(store))
        with _token_endpoint(_refused("invalid_scope")):
            assert await self._route(store) == 409
        assert _cred(store) == before

    async def test_route_answers_a_refused_client_as_a_bad_gateway(self, clock: Clock) -> None:
        store = _store()
        with _token_endpoint(_refused("invalid_client", 401)):
            assert await self._route(store) == 502
        assert _cred(store)[COUNT] == 1

    async def test_discovery(self, clock: Clock) -> None:
        store = _store()
        instance = _instance(authMode=MCPAuthMode.OAUTH.value)
        listing = AsyncMock(side_effect=MCPHttpStatusError(401, "expired"))
        with _token_endpoint(*[INVALID_GRANT] * 3), patch.object(discovery, "discover_tool_listing", new=listing):
            for _ in _spaced(clock, 3):
                with pytest.raises(MCPRefreshTokenInvalidError):
                    await discovery.discover_listing_for_owner(instance, _cred(store), USER, store)
        assert _cred(store)["deauthReason"] == "refresh_token_invalid"

    async def _agent_call(self, store: FakeConfigService, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = _FakeClientManagerFactory()
        factory.queued_responses = [[MCPHttpStatusError(401)]]
        monkeypatch.setattr(mcp_session_module, "MCPClientManager", factory)
        server = _server("oauth", auth=copy.deepcopy(_cred(store)), owner_id=USER)
        with pytest.raises(MCPRefreshTokenInvalidError):
            await MCPSessionManager(make_context(config_service=store)).call(server, "search", {})

    async def test_agent_through_its_session(self, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store()
        with _token_endpoint(*[INVALID_GRANT] * 3):
            for _ in _spaced(clock, 3):
                await self._agent_call(store, monkeypatch)
        assert _cred(store)["deauthReason"] == "refresh_token_invalid"

    async def test_background(self, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
        store = _store()
        service = svc_module.MCPTokenRefreshService(store)
        with _token_endpoint(*[INVALID_GRANT] * 3):
            for _ in _spaced(clock, 3):
                await service._refresh_all_tokens()
        assert _cred(store)["deauthReason"] == "refresh_token_invalid"
        assert fields(events(caplog, DEAUTH)[0])["trigger"] == "scan"
        assert service._invalid_refresh_failures == {}  # the shared rule counted, not the process

    async def test_strikes_from_different_paths_add_up(self, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store()
        service = svc_module.MCPTokenRefreshService(store)
        with _token_endpoint(*[INVALID_GRANT] * 3):
            clock.now = T0
            await service._refresh_all_tokens()
            clock.now = T0 + WINDOW
            await service._refresh_all_tokens()
            clock.now = T0 + 2 * WINDOW
            await self._agent_call(store, monkeypatch)
        assert _cred(store)["isAuthenticated"] is False

    async def test_background_still_signs_out_a_refused_request_shape_in_process(self, clock: Clock) -> None:
        store = _store()
        service = svc_module.MCPTokenRefreshService(store)
        with _token_endpoint(*[_refused("invalid_scope")] * 3):
            for _ in range(3):
                await service._refresh_all_tokens()
        assert (_cred(store)["isAuthenticated"], _cred(store)["deauthReason"]) == (False, "refresh_token_invalid")
        assert COUNT not in _cred(store)


class TestEECompatibility:
    """What OSS can pin of the names EE's MCPTokenRefreshService uses (ee/.../mcp_token_refresh_service.py:23, :152,
    :155-187); EE overrides `_refresh_credential` too, so its own call path is the EE port's to test."""

    def test_the_names_ee_imports_and_reads_are_kept(self) -> None:
        assert svc_module.MAX_REFRESH_TOKEN_INVALID_FAILURES == 3
        assert svc_module.MCPTokenRefreshService(FakeConfigService())._invalid_refresh_failures == {}

    async def test_an_org_aware_handler_override_is_still_called(self, clock: Clock) -> None:
        calls: list[tuple[str, str | None]] = []

        class EEStyle(svc_module.MCPTokenRefreshService):
            async def _handle_refresh_token_invalid(self, config_path: str, error: Exception, org_id: str | None = None) -> None:
                calls.append((config_path, org_id))

        store = _store()
        with _token_endpoint(_refused("invalid_scope")):
            await EEStyle(store)._refresh_all_tokens()
        assert calls == [(CRED, None)]
