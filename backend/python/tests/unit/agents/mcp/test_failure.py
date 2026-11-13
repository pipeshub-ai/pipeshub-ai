"""One reason for every way an MCP server can fail (`app.agents.mcp.failure`)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import MCPError

from app.agents.mcp import failure as mcp_failure
from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPHttpStatusError,
    MCPInsufficientScopeError,
    MCPLaunchRefusedError,
    MCPToolChangedError,
    MCPToolNotOfferedError,
    MCPUrlBlockedError,
)
from app.agents.mcp.failure import (
    MCPFailureReason,
    classify_mcp_failure,
    log_discover_end,
    mcp_error_class,
)
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError
from app.utils import logging_fields
from app.utils.credential_refresh_log import MISCONFIGURED_REPEAT_S
from app.utils.error_class import log_failure
from app.utils.logger import log_record_fields
from tests.unit.agents.mcp.refresh_failures import (
    lock_contention_error,
    token_endpoint_error,
    token_request_error,
)


def _http_error(status: int) -> MCPConnectionError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    error = MCPConnectionError(f"HTTP {status}")
    error.__cause__ = httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, request=request))
    return error


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (MCPTokenRefreshError("no refresh token"), MCPFailureReason.AUTH_EXPIRED),
        (MCPOAuthError("invalid_grant"), MCPFailureReason.AUTH_EXPIRED),
        (_http_error(401), MCPFailureReason.UNAUTHORIZED),
        (MCPInsufficientScopeError("needs more", scopes=["admin"]), MCPFailureReason.NEEDS_PERMISSION),
        # A 403 that signing in again can't fix stays what it was.
        (MCPHttpStatusError(403, challenge={"error": "insufficient_scope", "scope": "admin"}), MCPFailureReason.UNREACHABLE),
        (MCPUrlBlockedError("private address"), MCPFailureReason.BLOCKED),
        (MCPLaunchRefusedError("Shells cannot be used"), MCPFailureReason.BLOCKED),
        (asyncio.TimeoutError(), MCPFailureReason.TIMEOUT),
        (httpx.ReadTimeout("slow"), MCPFailureReason.TIMEOUT),
        (_http_error(503), MCPFailureReason.UNREACHABLE),
        (MCPConnectionError("Connection refused"), MCPFailureReason.UNREACHABLE),
        (httpx.ConnectError("refused"), MCPFailureReason.UNREACHABLE),
        (ConnectionResetError(), MCPFailureReason.UNREACHABLE),
        (RuntimeError("bug"), MCPFailureReason.ERROR),
    ],
)
def test_each_failure_has_its_reason(exc: BaseException, reason: MCPFailureReason) -> None:
    assert classify_mcp_failure(exc) is reason


def test_the_sdk_task_group_is_looked_through() -> None:
    assert classify_mcp_failure(ExceptionGroup("tg", [MCPUrlBlockedError("x")])) is MCPFailureReason.BLOCKED
    assert classify_mcp_failure(ExceptionGroup("tg", [asyncio.TimeoutError()])) is MCPFailureReason.TIMEOUT


def test_a_refused_launch_is_still_a_connection_error() -> None:
    # Callers that catch MCPConnectionError keep working.
    assert issubclass(MCPLaunchRefusedError, MCPConnectionError)


# A refresh the provider or another process stopped is not an expired sign-in: retrying can work.
_PROVIDER_DOWN = [
    pytest.param(503, {"error": "temporarily_unavailable"}, id="503"),
    pytest.param(502, {"error": "bad_gateway"}, id="502"),
    pytest.param(500, {"error": "server_error"}, id="500"),
    pytest.param(429, {"error": "slow_down"}, id="429"),
    # A permanent-sounding code from an overloaded endpoint isn't final either.
    pytest.param(503, {"error": "invalid_grant"}, id="503-invalid_grant"),
    pytest.param(429, {"error": "invalid_grant"}, id="429-invalid_grant"),
]


@pytest.mark.parametrize(("status", "body"), _PROVIDER_DOWN)
async def test_a_token_endpoint_outage_is_unreachable(status: int, body: dict[str, Any]) -> None:
    error = await token_endpoint_error(status, body)

    assert error.status == status
    assert classify_mcp_failure(error) is MCPFailureReason.UNREACHABLE
    assert classify_mcp_failure(ExceptionGroup("tg", [error])) is MCPFailureReason.UNREACHABLE


async def test_waiting_out_another_processs_refresh_is_unreachable() -> None:
    error = await lock_contention_error()

    assert classify_mcp_failure(error) is MCPFailureReason.UNREACHABLE
    assert classify_mcp_failure(ExceptionGroup("tg", [error])) is MCPFailureReason.UNREACHABLE


@pytest.mark.parametrize(("status", "body"), [
    pytest.param(400, {"error": "invalid_grant"}, id="400-invalid_grant"),
    pytest.param(401, {"error": "invalid_client"}, id="401-invalid_client"),
    pytest.param(200, {"error": "bad_refresh_token"}, id="200-github-style"),
    pytest.param(400, {"error": "invalid_request"}, id="400-invalid_request"),
    # Neither transient nor known to be permanent: still a sign-in problem.
    pytest.param(403, {"error": "access_denied"}, id="403"),
])
async def test_a_refused_refresh_still_asks_for_a_new_sign_in(status: int, body: dict[str, Any]) -> None:
    assert classify_mcp_failure(await token_endpoint_error(status, body)) is MCPFailureReason.AUTH_EXPIRED


@pytest.mark.parametrize("error", [
    MCPTokenRefreshError("No refresh token available at /services/mcp/credentials/i/u"),
    MCPTokenRefreshError("No OAuth client configuration found for /services/mcp/credentials/i/u"),
    MCPOAuthError("OAuth refresh response missing access_token"),
])
def test_a_refresh_failure_without_a_status_still_asks_for_a_new_sign_in(error: Exception) -> None:
    assert classify_mcp_failure(error) is MCPFailureReason.AUTH_EXPIRED


@pytest.mark.parametrize(("raised", "reason"), [
    (httpx.ConnectError("Name or service not known"), MCPFailureReason.UNREACHABLE),
    (httpx.ConnectError("Connection refused"), MCPFailureReason.UNREACHABLE),
    (httpx.ReadTimeout("slow"), MCPFailureReason.TIMEOUT),
    (httpx.ConnectTimeout("slow"), MCPFailureReason.TIMEOUT),
])
async def test_a_token_request_that_got_no_answer(raised: Exception, reason: MCPFailureReason) -> None:
    assert classify_mcp_failure(await token_request_error(raised)) is reason


class TestTheLogErrorClass:
    """`mcp_error_class`: `classify_error` first, then what the MCP reason means."""

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (MCPTokenRefreshError("no refresh token"), "auth_expired"),
            (MCPOAuthError("x", error_code="invalid_grant"), "auth_expired"),
            # The same rejected client as on the OpenAPI path: ours to fix.
            (MCPOAuthError("x", error_code="invalid_client"), "misconfigured"),
            (_http_error(401), "auth_expired"),
            (MCPHttpStatusError(401), "auth_expired"),
            (MCPInsufficientScopeError("needs more", scopes=["admin"]), "forbidden"),
            (MCPUrlBlockedError("private address"), "forbidden"),
            (MCPLaunchRefusedError("Shells cannot be used"), "forbidden"),
            (asyncio.TimeoutError(), "timeout"),
            (httpx.ReadTimeout("slow"), "timeout"),
            (ExceptionGroup("tg", [asyncio.TimeoutError()]), "timeout"),
            (MCPHttpStatusError(500), "upstream_5xx"),
            (MCPHttpStatusError(429), "rate_limited"),
            (MCPHttpStatusError(404), "not_found"),
            (MCPConnectionError("Connection refused"), "unavailable"),
            (ConnectionRefusedError(), "unavailable"),
            (MCPError(-32602, "bad sentinel"), "bad_request"),
            (MCPError(-32601, "no such method"), "bad_request"),
            (MCPError(-32603, "server broke"), "upstream_5xx"),
            (MCPToolNotOfferedError("gone"), "not_found"),
            (MCPToolChangedError("changed"), "conflict"),
            (KeyError("ours"), "internal"),
            (RuntimeError("?"), "internal"),
        ],
    )
    def test_each_failure(self, exc: BaseException, expected: str) -> None:
        assert mcp_error_class(exc) == expected

    def test_a_handshake_bug_wrapped_as_a_connection_error_reads_as_unavailable(self) -> None:
        """Pinned: `_connect_error` wraps whatever the SDK raised, our own bugs included."""
        error = MCPConnectionError("KeyError")
        error.__cause__ = KeyError("ours")
        assert mcp_error_class(error) == "unavailable"

    def test_the_callers_reason_is_used_when_given(self) -> None:
        assert mcp_error_class(RuntimeError("?"), MCPFailureReason.TIMEOUT) == "timeout"


def test_log_discover_end_names_its_source_and_takes_a_level(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("app.test.discover")
    caplog.set_level(logging.DEBUG, logger=log.name)

    log_discover_end(log, "mcp-1", MCPConnectionError("down"), source="listing", mcp_reason="unreachable")
    log_discover_end(log, "mcp-2", None, source="listing", reason="listing_deadline")
    log_discover_end(log, "mcp-3", MCPOAuthError("x", error_code="invalid_grant"), source="listing", level=logging.INFO)

    lines = [(r.levelno, log_record_fields(r)) for r in caplog.records]
    assert [(level, f["mcp_server_id"], f["source"], f["outcome"], f.get("error_class"), f.get("reason"))
            for level, f in lines] == [
        (logging.WARNING, "mcp-1", "listing", "failure", "unavailable", None),
        (logging.WARNING, "mcp-2", "listing", "skipped", None, "listing_deadline"),
        (logging.INFO, "mcp-3", "listing", "failure", "auth_expired", None),
    ]
    assert all(r.getMessage() == "mcp.discover.end" and "error_message" not in log_record_fields(r)
               for r in caplog.records)


class TestARefreshFailureOnATurnOrListing:
    """The refresh's own `credential_refresh.end` already logged it: the turn's and the listing's line keep their
    level (never demoted to DEBUG), say the class the `end` said, and a `misconfigured` one is ERROR once an hour."""

    LOG = logging.getLogger("app.test.discover")

    @pytest.fixture(autouse=True)
    def _fresh_gate(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.setattr(logging_fields, "_log_once_last", {})
        caplog.set_level(logging.DEBUG, logger=self.LOG.name)

    @staticmethod
    def _logged_at(level: int, exc: BaseException) -> BaseException:
        log_failure(logging.getLogger("app.test.refresh"), "credential_refresh.end", exc, level=level, include_error_text=False)
        return exc

    def _lines(self, caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
        return [(r.levelno, log_record_fields(r)["error_class"]) for r in caplog.records
                if r.name == self.LOG.name and r.getMessage() == "mcp.discover.end"]

    async def test_a_turns_line_keeps_its_level_without_a_stack_or_text(self, caplog: pytest.LogCaptureFixture) -> None:
        failure = self._logged_at(logging.WARNING, await token_endpoint_error(503, {"error": "temporarily_unavailable"}))

        log_discover_end(self.LOG, "mcp-1", failure, source="turn")

        [line] = [r for r in caplog.records if r.name == self.LOG.name]
        assert (line.levelno, line.exc_text) == (logging.WARNING, None)
        assert log_record_fields(line).items() >= {
            "error_class": "unavailable", "outcome": "failure", "error_type": "MCPOAuthError", "source": "turn",
        }.items()
        assert "error_message" not in log_record_fields(line)

    async def test_a_listings_sign_in_line_keeps_the_level_it_asks_for(self, caplog: pytest.LogCaptureFixture) -> None:
        failure = self._logged_at(logging.WARNING, await token_endpoint_error(400, {"error": "invalid_grant"}))

        log_discover_end(self.LOG, "mcp-1", failure, source="listing", level=logging.INFO)

        assert self._lines(caplog) == [(logging.INFO, "auth_expired")]

    def test_control_a_failure_nobody_logged_is_the_full_line(self, caplog: pytest.LogCaptureFixture) -> None:
        log_discover_end(self.LOG, "mcp-1", KeyError("ours"), source="turn")

        [line] = [r for r in caplog.records if r.name == self.LOG.name]
        assert (line.levelno, log_record_fields(line)["error_class"]) == (logging.ERROR, "internal")
        assert line.exc_text

    @pytest.mark.parametrize(("error", "reason", "cls"), [
        (MCPTokenRefreshError, "no_credential", "not_found"),
        (MCPTokenRefreshError, "credential_removed", "conflict"),
        (MCPTokenRefreshError, "config_missing", "misconfigured"),
        (MCPOAuthError, "url_blocked", "misconfigured"),
        (MCPOAuthError, "host_unresolved", "unavailable"),
    ])
    def test_a_tagged_refresh_failure_has_the_class_its_end_said(self, error: type, reason: str, cls: str) -> None:
        assert mcp_error_class(error("x", refresh_reason=reason)) == cls

    async def test_a_refused_request_shape_is_an_expired_sign_in_not_a_bad_request(self) -> None:
        assert mcp_error_class(await token_endpoint_error(400, {"error": "invalid_request"})) == "auth_expired"

    def test_a_misconfigured_line_is_an_error_once_an_hour_per_server(self, caplog: pytest.LogCaptureFixture) -> None:
        def turn(server: str) -> None:
            log_discover_end(self.LOG, server, MCPTokenRefreshError("x", refresh_reason="config_missing"), source="turn")

        turn("mcp-s")
        turn("mcp-s")
        turn("mcp-t")
        for key in logging_fields._log_once_last:
            logging_fields._log_once_last[key] -= MISCONFIGURED_REPEAT_S + 1
        turn("mcp-s")

        assert [level for level, _ in self._lines(caplog)] == [logging.ERROR, logging.DEBUG, logging.ERROR, logging.ERROR]
        assert all(cls == "misconfigured" for _, cls in self._lines(caplog))

    @pytest.mark.parametrize("kwargs", [{"status": 503}, {"refresh_reason": "host_unresolved"}], ids=["outage", "dns"])
    def test_other_classes_are_never_gated(self, caplog: pytest.LogCaptureFixture, kwargs: dict[str, Any]) -> None:
        exc = MCPOAuthError("x", **kwargs)
        log_discover_end(self.LOG, "mcp-s", exc, source="turn")
        log_discover_end(self.LOG, "mcp-s", exc, source="listing")

        assert self._lines(caplog) == [(logging.WARNING, "unavailable")] * 2
        assert mcp_failure.misconfigured_level("mcp-s", "unavailable") is None
