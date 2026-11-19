"""An API toolset call ends in one `tool_call.end` from the executor, by class and status, and a
401 that a token refresh fixes is one `openapi.retry` warning (`openapi_tool_adapter.py`, `openapi/executor.py`)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.openapi_tool_adapter import OpenAPIToolAdapter
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.openapi.auth import ApiToolsetAuthError, AuthApplier, NoAuth
from app.agents.openapi.executor import ApiCallError, OpenAPIHttpExecutor
from app.agents.openapi.models import CompiledOperation
from app.agents.openapi.request_builder import RequestBuildError
from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.asyncio

_SENTINEL = "sentinel-o9"
_INSTANCE = SimpleNamespace(id="ts-1", name="Acme", use_shared_credential=False)
_OPERATION = CompiledOperation(tool_name="get_ticket", method="GET", path="/tickets", input_schema={"type": "object"})


class _RefreshingAuth(AuthApplier):
    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        request.headers["Authorization"] = "Bearer new" if force_refresh else "Bearer old"

    @property
    def can_refresh(self) -> bool:
        return True


class _Raises:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def call(self, _operation: object, _arguments: object) -> None:
        raise self._exc


def _http(handler: Callable[[httpx.Request], httpx.Response], auth: AuthApplier | None = None) -> OpenAPIHttpExecutor:
    return OpenAPIHttpExecutor(
        "https://api.acme.test", auth or NoAuth(), allow_private=False, transport=httpx.MockTransport(handler),
    )


async def _run(executor: Any, caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:  # noqa: ANN401
    adapter = OpenAPIToolAdapter(_INSTANCE, _OPERATION, executor, name="api_acme_get_ticket")  # type: ignore[arg-type]
    registry = ToolRegistry()
    registry.register_tool(adapter)
    with caplog.at_level(logging.DEBUG):
        await ToolExecutor(registry).call_tool(ToolCall(id="t1", name=adapter.name, arguments={}))
    (record,) = [r for r in caplog.records if r.getMessage() == "tool_call.end"]
    assert _SENTINEL not in caplog.text
    return record, log_record_fields(record)


def _raise(exc: BaseException) -> Callable[[httpx.Request], httpx.Response]:
    def _handler(_request: httpx.Request) -> httpx.Response:
        raise exc
    return _handler


async def test_a_404_says_status_and_class_but_not_the_body(caplog: pytest.LogCaptureFixture) -> None:
    record, fields = await _run(_http(lambda r: httpx.Response(404, json={"error": _SENTINEL})), caplog)

    assert record.levelno == logging.WARNING
    assert {k: fields[k] for k in ("tool", "toolset_id", "kind", "outcome", "reason", "status", "error_class")} == {
        "tool": "api_acme_get_ticket", "toolset_id": "ts-1", "kind": "openapi", "outcome": "failure",
        "reason": "tool_error", "status": 404, "error_class": "not_found",
    }


async def test_a_redirect_is_a_warning_with_no_error_class(caplog: pytest.LogCaptureFixture) -> None:
    record, fields = await _run(_http(lambda r: httpx.Response(302, headers={"location": "https://x.test/"})), caplog)

    assert record.levelno == logging.WARNING
    assert fields["status"] == 302 and "error_class" not in fields
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_refreshed_401_is_one_retry_warning_then_a_success(caplog: pytest.LogCaptureFixture) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.headers["Authorization"] == "Bearer new" else 401, json={})

    record, fields = await _run(_http(_handler, _RefreshingAuth()), caplog)

    (retry,) = [r for r in caplog.records if r.getMessage() == "openapi.retry"]
    assert retry.levelno == logging.WARNING
    assert log_record_fields(retry) == {
        "tool": "api_acme_get_ticket", "tool_call_id": "t1", "toolset_id": "ts-1", "status": 401, "reason": "token_refresh",
    }
    assert record.levelno == logging.INFO and fields["outcome"] == "success"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(("exc", "error_class"), [
    (httpx.ConnectError(_SENTINEL), "unavailable"),
    (httpx.ReadTimeout(_SENTINEL), "timeout"),
    (MCPUrlBlockedError(_SENTINEL), "forbidden"),
])
async def test_a_call_with_no_answer_is_one_warning(
    exc: BaseException, error_class: str, caplog: pytest.LogCaptureFixture,
) -> None:
    record, fields = await _run(_http(_raise(exc)), caplog)

    assert record.levelno == logging.WARNING and record.exc_text is None
    assert (fields["error_class"], fields["error_type"]) == (error_class, "ApiCallError")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == [record]


def _auth_error(cause: BaseException | None) -> ApiToolsetAuthError:
    error = ApiToolsetAuthError("The sign-in for this API action has expired.")
    error.__cause__ = cause
    return error


def _no_answer(cause: BaseException) -> ApiCallError:
    error = ApiCallError("Could not reach the API (DecodingError).")
    error.__cause__ = cause
    return error


@pytest.mark.parametrize(("exc", "level", "error_class", "reason"), [
    (RequestBuildError(_SENTINEL), logging.WARNING, "bad_request", "bad_args"),
    (_auth_error(None), logging.WARNING, "auth_expired", "tool_error"),
    (_auth_error(MCPOAuthError(_SENTINEL, error_code="invalid_grant")), logging.WARNING, "auth_expired", "tool_error"),
    # The provider's token endpoint failing is upstream, not our bug: no ERROR, no stack.
    (_auth_error(MCPOAuthError(f"Token endpoint returned HTTP 500: {_SENTINEL}")), logging.WARNING, "auth_expired", "tool_error"),
    (_auth_error(MCPOAuthError(_SENTINEL, error_code="invalid_request")), logging.WARNING, "auth_expired", "tool_error"),
    (_auth_error(MCPOAuthError(f"Token response had no access_token: {_SENTINEL}")), logging.WARNING, "auth_expired", "tool_error"),
    (_no_answer(httpx.DecodingError(_SENTINEL)), logging.WARNING, "unavailable", "tool_error"),
    # The same rejected client as on the MCP path: ours to fix.
    (_auth_error(MCPOAuthError(_SENTINEL, error_code="invalid_client")), logging.ERROR, "misconfigured", "tool_error"),
    (RuntimeError(_SENTINEL), logging.ERROR, "internal", "exception"),
])
async def test_each_failure_is_logged_once_by_class(
    exc: BaseException, level: int, error_class: str, reason: str, caplog: pytest.LogCaptureFixture,
) -> None:
    record, fields = await _run(_Raises(exc), caplog)

    assert record.levelno == level
    assert (record.exc_text is not None) == (level >= logging.ERROR)
    assert (fields["error_class"], fields["reason"]) == (error_class, reason)
    assert "error_message" not in fields
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == [record]


async def test_the_adapter_says_what_kind_of_tool_it_is() -> None:
    adapter = OpenAPIToolAdapter(_INSTANCE, _OPERATION, _Raises(RuntimeError()), name="api_acme_get_ticket")  # type: ignore[arg-type]

    assert adapter.log_kind == "openapi"
    assert adapter.log_ids == {"toolset_id": "ts-1"}
