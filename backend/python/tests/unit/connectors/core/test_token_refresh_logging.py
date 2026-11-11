"""What the connector token refresh service logs: one refresh of one credential, from the logs alone.

The real OAuthProvider sends each refresh to a scripted token endpoint (it replaces
aiohttp.ClientSession in oauth_service), so the lines are the ones production writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service import token_refresh_service as trs
from app.connectors.core.base.token_service.oauth_service import (
    OAuthTokenRequestError,
    RefreshTokenInvalidError,
)
from app.core import org_context as oc
from app.utils import credential_refresh_log as CR
from app.utils import logging_fields
from app.utils import request_context as rc
from app.utils.structured_logging import DefaultLoggingProvider
from tests.support.refresh_log_fakes import (
    INVALID_GRANT,
    SEEDED_RT,
    SEEDED_SECRET,
    FakeConfigStore,
    FakeTokenEndpoint,
)
from tests.support.refresh_log_fakes import assert_no_secrets as _assert_no_secrets
from tests.support.refresh_log_fakes import events as _events
from tests.support.refresh_log_fakes import fields as _fields

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = "connector_service.refresh"
TYPE = "testapp"


def _key(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/config"


def _config(*, expires_in: int = 3600, age_s: float = 0, client: bool = True) -> dict:
    auth = {"clientId": "client-1", "clientSecret": SEEDED_SECRET} if client else {}
    return {
        "auth": {**auth, "authorizeUrl": "https://idp.example/authorize", "tokenUrl": "https://idp.example/token",
                 "redirectUri": "http://localhost/cb"},
        "credentials": {"access_token": "at-old", "refresh_token": SEEDED_RT, "expires_in": expires_in,
                        "created_at": (datetime.now() - timedelta(seconds=age_s)).isoformat()},  # noqa: DTZ005 - OAuthToken is naive
    }


def _expired() -> dict:
    return _config(expires_in=3600, age_s=7200)


def _doc(connector_id: str, org: str) -> dict:
    return {"_key": connector_id, "type": TYPE, "authType": "OAUTH", "orgId": org}


@pytest.fixture(autouse=True)
def _raw_identity_and_fresh_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setattr(logging_fields, "_log_once_last", {})
    monkeypatch.setattr(trs, "CREDENTIAL_SAVE_RETRY_DELAY_SECONDS", 0)
    # Importing any *_main module sets a process-wide suffix on request_id (not on scheduled_by, a bare root).
    monkeypatch.setattr(rc, "_service_suffix", "")


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> FakeTokenEndpoint:
    ep = FakeTokenEndpoint()
    monkeypatch.setattr(oauth_service, "ClientSession", ep.client_session)
    return ep


@pytest.fixture
def config() -> FakeConfigStore:
    return FakeConfigStore()


@pytest.fixture
def graph() -> MagicMock:
    g = MagicMock()
    g.update_node = AsyncMock(return_value=True)
    g.get_document = AsyncMock(return_value=None)
    g.get_edges_to_node = AsyncMock(return_value=[])
    g.get_all_orgs = AsyncMock(return_value=[])
    g.get_nodes_by_filters = AsyncMock(return_value=[])
    return g


@pytest.fixture
async def service(config: FakeConfigStore, graph: MagicMock, caplog: pytest.LogCaptureFixture) -> Iterator[trs.TokenRefreshService]:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    svc = trs.TokenRefreshService(config, graph)  # type: ignore[arg-type]
    svc._running = True
    yield svc
    await svc.stop()


def _connectors(graph: MagicMock, *docs: dict) -> None:
    """The scan lists the orgs, then reads each org's OAuth connectors with an ``orgId`` filter."""
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": org} for org in dict.fromkeys(d["orgId"] for d in docs)])
    graph.get_nodes_by_filters = AsyncMock(side_effect=lambda _c, f: [
        d for d in docs if d["orgId"] == f["orgId"]] if f["authType"] == "OAUTH" else [])


class TestScheduledRefresh:
    async def test_timer_firing_is_its_own_unit_of_work_with_who_and_why(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        endpoint: FakeTokenEndpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        config.store[_key("c1")] = _config()
        _connectors(graph, _doc("c1", "o1"))
        real_delay = service._calculate_refresh_delay
        service._calculate_refresh_delay = lambda token: (0.01, real_delay(token)[1])  # fire at once, whatever the load

        await service._refresh_all_tokens()
        service._calculate_refresh_delay = real_delay
        [armed] = _events(caplog, CR.EVENT_SCHEDULED)
        timer = service._refresh_tasks["c1"]
        config.store[_key("c1")] = _expired()
        caplog.clear()
        with contextlib.suppress(asyncio.CancelledError):  # the timer re-arms itself, cancelling its own task
            await asyncio.wait_for(timer, 5)

        [end] = _events(caplog, CR.EVENT_END)
        fields = _fields(end)
        assert end.levelno == logging.INFO
        assert fields.items() >= {
            "outcome": "success", "refresh_kind": "connector", "trigger": "scheduled", "connector_id": "c1",
            "connector_type": TYPE, "provider": TYPE, "org_id": "o1", "ctx_kind": "job",
            "scheduled_by": armed.request_id,
        }.items()
        assert isinstance(fields["duration_ms"], int)
        assert end.request_id.startswith("sys-credential_refresh-")
        assert end.request_id != armed.request_id
        assert _fields(armed).items() >= {"trigger": "scan", "org_id": "o1", "connector_id": "c1"}.items()
        # One refresh is two INFO lines: its end and the next schedule.
        info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
        assert info == [CR.EVENT_END, CR.EVENT_SCHEDULED]

    async def test_scan_rearming_the_same_token_is_debug_and_orgs_do_not_leak(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config.store[_key("c1")] = _config()
        config.store[_key("c2")] = _config()
        _connectors(graph, _doc("c1", "o1"), _doc("c2", "o2"))

        await service._refresh_all_tokens()
        await service._refresh_all_tokens()
        config.store[_key("c1")] = _config(age_s=60)  # a new token: a new refresh time
        await service._refresh_all_tokens()

        lines = [(_fields(r)["connector_id"], _fields(r)["org_id"], r.levelno) for r in _events(caplog, CR.EVENT_SCHEDULED)]
        assert lines == [
            ("c1", "o1", logging.INFO), ("c2", "o2", logging.INFO),
            ("c1", "o1", logging.DEBUG), ("c2", "o2", logging.DEBUG),
            ("c1", "o1", logging.INFO), ("c2", "o2", logging.DEBUG),
        ]
        assert oc.peek() is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    async def test_oauth_callback_schedule_names_the_connector(
        self, service: trs.TokenRefreshService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        await service.schedule_token_refresh("c1", TYPE, oauth_service.OAuthToken(access_token="a", refresh_token="r", expires_in=3600))

        [line] = _events(caplog, CR.EVENT_SCHEDULED)
        assert line.levelno == logging.INFO
        assert _fields(line).items() >= {"connector_id": "c1", "refresh_kind": "connector"}.items()
        assert _fields(line)["next_refresh_at"].endswith("Z")

    async def test_new_token_already_due_is_a_warning_not_an_error(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [(200, json.dumps({"access_token": "a2", "refresh_token": "r2", "expires_in": 600}))]
        config.store[_key("c1")] = _config(expires_in=600)

        await service.schedule_token_refresh("c1", TYPE, oauth_service.OAuthToken(access_token="a", refresh_token=SEEDED_RT, expires_in=600))

        [line] = _events(caplog, CR.EVENT_NOT_SCHEDULED)
        assert (line.levelno, _fields(line)["reason"]) == (logging.WARNING, "new_token_due")
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestFailedRefresh:
    @pytest.mark.parametrize(("answer", "cls", "status", "error_type"), [
        ((503, f"upstream overloaded, retry ({SEEDED_RT})"), "unavailable", 503, OAuthTokenRequestError),
        ((502, "bad gateway"), "upstream_5xx", 502, OAuthTokenRequestError),
        ((429, "slow down"), "rate_limited", 429, OAuthTokenRequestError),
        ((403, "blocked by WAF"), "forbidden", 403, Exception),  # the bare 403 wrapper oauth_service raises
        (ConnectionRefusedError("refused"), "unavailable", None, ConnectionRefusedError),
        (TimeoutError(), "timeout", None, TimeoutError),
    ])
    async def test_on_demand_failure_is_one_classified_end_and_is_raised(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture, answer: object, cls: str, status: int | None, error_type: type[Exception],
    ) -> None:
        endpoint.answers = [answer]
        config.store[_key("c1")] = _config()

        with pytest.raises(error_type) as raised:
            await service.refresh_now("c1", TYPE, SEEDED_RT)

        [end] = _events(caplog, CR.EVENT_END)
        assert type(raised.value) is error_type
        assert end.levelno == logging.WARNING
        assert end.exc_info is None
        assert _fields(end).items() >= {
            "outcome": "failure", "error_class": cls, "error_type": error_type.__name__, "trigger": "on_demand", "connector_id": "c1",
        }.items()
        assert _fields(end).get("http_status") == status
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        _assert_no_secrets(caplog)

    async def test_revoked_grant_three_scans_strike_then_deauthenticate(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        endpoint: FakeTokenEndpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [INVALID_GRANT] * 3
        config.store[_key("c1")] = _expired()
        _connectors(graph, _doc("c1", "o1"))
        graph.get_document = AsyncMock(return_value={"orgId": "o1", "type": "Test App"})
        published_under: list[str | None] = []
        service.set_messaging_producer(MagicMock(send_message=AsyncMock(
            side_effect=lambda **_: published_under.append(oc.peek().org_id if oc.peek() else None),
        )))

        for _ in range(trs.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._refresh_all_tokens()

        # The one effect beyond logging: the appDisabled envelope now carries the connector's org (signed when ENVELOPE_SIGN=true).
        assert published_under == ["o1"]
        ends = _events(caplog, CR.EVENT_END)
        assert [r.levelno for r in ends] == [logging.WARNING] * 3
        assert {(f["error_class"], f["reason"], f["oauth_error"], f["http_status"]) for f in map(_fields, ends)} == {
            ("auth_expired", "invalid_grant", "invalid_grant", 400),
        }
        assert [
            (r.levelno, _fields(r)["strike"], _fields(r)["counted"], _fields(r)["error_class"], _fields(r)["oauth_error"])
            for r in _events(caplog, CR.EVENT_STRIKE)
        ] == [(logging.INFO, f"{n}/3", True, "auth_expired", "invalid_grant") for n in (1, 2, 3)]
        [deauth] = _events(caplog, CR.EVENT_DEAUTHENTICATED)
        assert deauth.levelno == logging.WARNING
        assert _fields(deauth).items() >= {
            "reason": "refresh_token_invalid", "strikes": 3, "app_disabled_event": "sent",
            "connector_id": "c1", "org_id": "o1", "trigger": "scan",
        }.items()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        _assert_no_secrets(caplog)

    @pytest.mark.parametrize(("break_it", "event", "cls"), [
        (lambda svc, graph: setattr(graph, "update_node", AsyncMock(side_effect=RuntimeError("graph down"))),
         CR.EVENT_DEAUTHENTICATE_FAILED, "internal"),
        (lambda svc, graph: svc.set_messaging_producer(MagicMock(send_message=AsyncMock(side_effect=TypeError("bad message")))),
         CR.EVENT_APP_DISABLED_EVENT_FAILED, "internal"),
    ])
    async def test_failure_while_handling_a_rejection_keeps_the_provider_text_out_of_the_stack(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        endpoint: FakeTokenEndpoint, caplog: pytest.LogCaptureFixture, break_it: object, event: str, cls: str,
    ) -> None:
        # Review F1: the deauth runs inside `except RefreshTokenInvalidError`, so its error carries the rejection
        # (token URL, redirect URI, body echoing the refresh token) as __context__.
        endpoint.answers = [INVALID_GRANT] * 3
        config.store[_key("c1")] = _expired()
        _connectors(graph, _doc("c1", "o1"))
        graph.get_document = AsyncMock(return_value={"orgId": "o1", "type": "Test App"})
        break_it(service, graph)

        for _ in range(trs.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._refresh_all_tokens()

        [line] = _events(caplog, event)
        # Classed by its own error, not by the rejection it interrupted; the rejection is on the attempt's end line.
        assert (line.levelno, _fields(line)["error_class"]) == (logging.ERROR, cls)
        assert "http_status" not in _fields(line)
        assert line.exc_text
        assert "RefreshTokenInvalidError" not in line.exc_text
        _assert_no_secrets(caplog)

    async def test_a_bug_in_the_strike_handler_on_the_scan_is_internal_not_an_expired_grant(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        endpoint: FakeTokenEndpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Review R1: handled inside `except RefreshTokenInvalidError`, the KeyError was chained to the rejection and classed by it.
        endpoint.answers = [INVALID_GRANT]
        config.store[_key("c1")] = _expired()
        _connectors(graph, _doc("c1", "o1"))
        service._stored_refresh_token = AsyncMock(side_effect=KeyError("bug"))

        await service._refresh_all_tokens()

        [line] = _events(caplog, CR.EVENT_CHECK_FAILED)
        assert (line.levelno, _fields(line)["error_class"], _fields(line)["error_type"]) == (logging.ERROR, "internal", "KeyError")
        assert "reason" not in _fields(line)
        _assert_no_secrets(caplog)

    async def test_a_deauth_failure_keeps_its_own_cause_in_the_stack(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        endpoint: FakeTokenEndpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Review R2: the graph driver wraps a refused connection without `from`; that context is the real cause.
        async def update_node(*_: object) -> bool:
            try:
                raise ConnectionRefusedError("graph refused")
            except ConnectionRefusedError:
                raise RuntimeError("update failed")  # noqa: B904 - the implicit context is the case under test

        endpoint.answers = [INVALID_GRANT] * 3
        config.store[_key("c1")] = _expired()
        _connectors(graph, _doc("c1", "o1"))
        graph.update_node = update_node

        for _ in range(trs.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            await service._refresh_all_tokens()

        [line] = _events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, _fields(line)["error_class"]) == (logging.ERROR, "unavailable")
        assert "ConnectionRefusedError: graph refused" in line.exc_text
        _assert_no_secrets(caplog)

    async def test_strike_handler_failing_in_a_timer_keeps_the_provider_text_out(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [INVALID_GRANT]
        config.store[_key("c1")] = _expired()
        service._stored_refresh_token = AsyncMock(side_effect=RuntimeError("config store down"))
        armed_by = rc.set_context("req-arming")
        try:
            await service._delayed_refresh("c1", TYPE, 0)
        finally:
            rc.reset_context(armed_by)

        [line] = _events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.ERROR
        assert _fields(line).items() >= {
            "connector_id": "c1", "trigger": "scheduled", "error_class": "internal", "scheduled_by": "req-arming",
        }.items()
        # Caught after fresh_root exits, the line carried the arming request's root and no scheduled_by.
        assert line.request_id.startswith("sys-credential_refresh-")
        _assert_no_secrets(caplog)

    async def test_timer_whose_root_cannot_be_bound_logs_one_failure_and_does_not_raise(
        self, service: trs.TokenRefreshService, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(trs, "fresh_root", MagicMock(side_effect=RuntimeError("context broken")))

        await service._delayed_refresh("c1", TYPE, 0)

        [line] = _events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.ERROR
        assert _fields(line).items() >= {"connector_id": "c1", "trigger": "scheduled", "error_type": "RuntimeError"}.items()

    async def test_rejected_client_strikes_carry_their_cause_after_the_error_line(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # QA F2/F1: on demand, invalid_client is ERROR once, then its repeats are DEBUG. At INFO, the strikes still say why.
        endpoint.answers = [(401, f'{{"error": "invalid_client", "error_description": "client of {SEEDED_RT} unknown"}}')] * 3
        config.store[_key("c1")] = _config()
        caplog.set_level(logging.INFO, logger=LOGGER)

        for _ in range(trs.MAX_REFRESH_TOKEN_INVALID_FAILURES):
            with pytest.raises(RefreshTokenInvalidError):
                await service.refresh_now("c1", TYPE, SEEDED_RT)

        assert [r.levelno for r in _events(caplog, CR.EVENT_END)] == [logging.ERROR]
        assert {(_fields(r)["error_class"], _fields(r)["oauth_error"]) for r in _events(caplog, CR.EVENT_STRIKE)} == {
            ("misconfigured", "invalid_client"),
        }
        [deauth] = _events(caplog, CR.EVENT_DEAUTHENTICATED)
        assert _fields(deauth)["trigger"] == "on_demand"
        assert "path" not in _fields(deauth)
        _assert_no_secrets(caplog)

    async def test_superseded_rejection_is_a_strike_that_does_not_count(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        config.store[_key("c1")] = {"credentials": {"refresh_token": "rt-rotated"}}

        await service._handle_refresh_token_invalid("c1", RefreshTokenInvalidError("rejected"), SEEDED_RT)

        [strike] = _events(caplog, CR.EVENT_STRIKE)
        assert (_fields(strike)["strike"], _fields(strike)["counted"], _fields(strike)["error_class"]) == (
            "0/3", False, "auth_expired",
        )
        assert "c1" not in service._invalid_refresh_failures

    async def test_missing_client_credentials_are_an_error_once_per_hour(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config.store[_key("c1")] = {**_expired(), "auth": {}}
        _connectors(graph, _doc("c1", "o1"))

        await service._refresh_all_tokens()
        await service._refresh_all_tokens()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert [r.getMessage() for r in errors] == [CR.EVENT_END]
        assert _fields(errors[0]).items() >= {"error_class": "misconfigured", "reason": "config_missing"}.items()
        assert errors[0].exc_text
        assert [r.levelno for r in _events(caplog, CR.EVENT_END)] == [logging.ERROR, logging.DEBUG]
        assert [r.levelno for r in _events(caplog, CR.EVENT_CHECK_FAILED)] == [logging.DEBUG, logging.DEBUG]

    async def test_save_failure_is_an_internal_error_with_a_stack(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        config.store[_key("c1")] = _config()
        config.save_result = False

        with pytest.raises(trs.CredentialSaveError):
            await service.refresh_now("c1", TYPE, SEEDED_RT)

        [end] = _events(caplog, CR.EVENT_END)
        assert end.levelno == logging.ERROR
        assert end.exc_text
        assert _fields(end).items() >= {"error_class": "internal", "reason": "save_failed"}.items()
        retries = [r for r in caplog.records if r.getMessage() == "Saving the refreshed credentials failed"]
        assert [(r.levelno, _fields(r)["attempt"]) for r in retries] == [(logging.WARNING, n) for n in (1, 2, 3)]

    async def test_recursion_error_is_one_error_line_without_a_stack(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, graph: MagicMock,
        endpoint: FakeTokenEndpoint, caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = [RecursionError("maximum recursion depth exceeded")]
        config.store[_key("c1")] = _expired()
        _connectors(graph, _doc("c1", "o1"))

        await service._refresh_all_tokens()

        [error] = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error.getMessage() == CR.EVENT_END
        assert error.exc_info is None
        assert _fields(error)["reason"] == "recursion"
        assert [r.levelno for r in _events(caplog, CR.EVENT_CHECK_FAILED)] == [logging.DEBUG]


class TestOneEndPerAttempt:
    async def test_direct_choke_point_call_still_names_the_connector(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Salesforce calls _perform_token_refresh itself.
        endpoint.answers = ["ok"]
        config.store[_key("c1")] = _config()

        await service._perform_token_refresh("c1", TYPE, SEEDED_RT)

        [end] = _events(caplog, CR.EVENT_END)
        assert _fields(end).items() >= {"outcome": "success", "connector_id": "c1", "trigger": "on_demand"}.items()

    async def test_adopted_token_is_a_success_without_a_request(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config.store[_key("c1")] = {**_config(), "credentials": {**_config()["credentials"], "refresh_token": "rt-rotated"}}

        await service.refresh_now("c1", TYPE, SEEDED_RT)

        [end] = _events(caplog, CR.EVENT_END)
        assert (_fields(end)["outcome"], _fields(end)["reason"]) == ("success", "adopted")
        assert endpoint.sent == []

    async def test_load_failure_is_a_check_failure_not_an_end(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, caplog: pytest.LogCaptureFixture,
    ) -> None:
        config.read_error = ConnectionError("config store down")

        await service._process_connectors_for_refresh([_doc("c1", "o1")])

        assert _events(caplog, CR.EVENT_START) == []
        assert _events(caplog, CR.EVENT_END) == []
        [line] = _events(caplog, CR.EVENT_CHECK_FAILED)
        assert line.levelno == logging.WARNING
        assert _fields(line).items() >= {"error_class": "unavailable", "connector_id": "c1", "trigger": "scan", "org_id": "o1"}.items()

    async def test_failure_after_a_successful_refresh_is_one_success_and_one_check_failure(
        self, service: trs.TokenRefreshService, config: FakeConfigStore, endpoint: FakeTokenEndpoint,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        endpoint.answers = ["ok"]
        config.store[_key("c1")] = _expired()
        service.schedule_token_refresh = AsyncMock(side_effect=KeyError("refresh_time"))

        await service._process_connectors_for_refresh([_doc("c1", "o1")])

        assert [_fields(r)["outcome"] for r in _events(caplog, CR.EVENT_END)] == ["success"]
        [line] = _events(caplog, CR.EVENT_CHECK_FAILED)
        assert (line.levelno, _fields(line)["error_class"], bool(line.exc_text)) == (logging.ERROR, "internal", True)


class TestDeauthentication:
    async def test_failed_state_write_is_an_error_with_a_stack(
        self, service: trs.TokenRefreshService, graph: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        graph.update_node = AsyncMock(side_effect=RuntimeError("graph down"))

        await service._mark_connector_unauthenticated("c1")

        [line] = _events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, bool(line.exc_text)) == (logging.ERROR, True)
        assert _events(caplog, CR.EVENT_DEAUTHENTICATED) == []

    async def test_state_write_that_changed_nothing_is_an_error(
        self, service: trs.TokenRefreshService, graph: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        graph.update_node = AsyncMock(return_value=False)

        await service._mark_connector_unauthenticated("c1")

        [line] = _events(caplog, CR.EVENT_DEAUTHENTICATE_FAILED)
        assert (line.levelno, _fields(line)["outcome"], _fields(line)["reason"]) == (logging.ERROR, "failure", "not_updated")

    @pytest.mark.parametrize(("send", "sent", "failure_level"), [
        (AsyncMock(), "sent", None),
        (AsyncMock(side_effect=ConnectionError("broker down")), "direct_write", logging.WARNING),
        (AsyncMock(side_effect=TypeError("bad message")), "direct_write", logging.ERROR),
    ])
    async def test_app_disabled_event_outcome_is_on_the_deauthenticated_line(
        self, service: trs.TokenRefreshService, graph: MagicMock, caplog: pytest.LogCaptureFixture,
        send: AsyncMock, sent: str, failure_level: int | None,
    ) -> None:
        graph.get_document = AsyncMock(return_value={"orgId": "o1", "type": "Test App", "appGroup": "g", "scope": "team"})
        service.set_messaging_producer(MagicMock(send_message=send))

        await service._mark_connector_unauthenticated("c1")

        [deauth] = _events(caplog, CR.EVENT_DEAUTHENTICATED)
        assert _fields(deauth)["app_disabled_event"] == sent
        assert [r.levelno for r in _events(caplog, CR.EVENT_APP_DISABLED_EVENT_FAILED)] == (
            [failure_level] if failure_level else []
        )
        if failure_level is None:
            assert send.await_args.kwargs["message"]["payload"]["orgId"] == "o1"
