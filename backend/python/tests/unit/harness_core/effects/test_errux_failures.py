"""EU-1, EU-2: a failed tool call carries a typed code beside the detail the model reads (QA17-O1)."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from harness_core.contracts.generated.enums import TOOL_ERROR_CODES
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import (
    EffectCall,
    EffectRunner,
    RequestEffectLog,
    ToolEffect,
    args_digest,
    tool_id_for,
)
from harness_core.effects.failures import ToolFailure, tool_error_code
from harness_core.effects.results import encode_result
from harness_core.effects.types import ToolExecution
from harness_core.kernel.core.messages import ToolCall
from harness_core.testing.contracts.effect_log import context

# Built at runtime: a literal token shape trips secret scanners, and the redactor only needs the pattern.
TOKEN = "-".join(["xoxb", "0" * 12, "0" * 12, "FAKE" * 6])


def _named(name: str, base: type[Exception] = Exception, **attrs: object) -> Exception:
    """An exception of a client the harness does not import, by its type name, as the classifier sees it."""
    cls = type(name, (base,), {})
    exc = cls("the dependency's own words, which never decide the code")
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _wrapped(inner: BaseException) -> Exception:
    """A client that wraps a transport failure in its own exception (qdrant's ResponseHandlingException)."""
    try:
        raise inner
    except BaseException as cause:
        outer = _named("ResponseHandlingException")
        outer.__cause__ = cause
        return outer


CASES: list[tuple[str, BaseException, str]] = [
    ("refused connection", ConnectionRefusedError(111, "Connection refused"), "unavailable"),
    ("reset connection", ConnectionResetError(104, "reset"), "unavailable"),
    ("dns", socket.gaierror(-2, "Name or service not known"), "unavailable"),
    ("httpx connect error", _named("ConnectError"), "unavailable"),
    ("httpx connect timeout", _named("ConnectTimeout", TimeoutError), "unavailable"),
    ("aiohttp server disconnected", _named("ServerDisconnectedError"), "unavailable"),
    ("openai connection error", _named("APIConnectionError"), "unavailable"),
    ("neo4j service unavailable", _named("ServiceUnavailable"), "unavailable"),
    ("mcp connection error", _named("MCPConnectionError"), "unavailable"),
    ("a client wrapping a connect error", _wrapped(_named("ConnectError")), "unavailable"),
    ("timeout", TimeoutError(), "timeout"),
    ("asyncio timeout", asyncio.TimeoutError(), "timeout"),
    ("httpx read timeout", _named("ReadTimeout"), "timeout"),
    ("openai timeout", _named("APITimeoutError"), "timeout"),
    ("429", _named("HTTPError", status_code=429), "rate_limited"),
    ("openai rate limit", _named("RateLimitError"), "rate_limited"),
    ("401", _named("HTTPError", status=401), "auth"),
    ("403 on the response", _named("HTTPStatusError", response=_Response(403)), "auth"),
    ("openai authentication", _named("AuthenticationError"), "auth"),
    ("404", _named("HTTPError", status_code=404), "not_found"),
    ("502", _named("HTTPError", status_code=502), "unavailable"),
    ("503", _named("HTTPError", status_code=503), "unavailable"),
    ("504", _named("HTTPError", status_code=504), "timeout"),
    ("a tool's own code", ToolFailure("not_found", "no such channel"), "not_found"),
    ("anything else", ValueError("bad query"), "failed"),
    ("a message that mentions a timeout", RuntimeError("connection refused, timed out, 503"), "failed"),
    ("a status that is a bool", _named("Weird", status=True), "failed"),
]


@pytest.mark.parametrize(("exc", "code"), [(c[1], c[2]) for c in CASES], ids=[c[0] for c in CASES])
def test_eu_1_a_failure_is_typed_by_its_class_and_status_never_its_text(exc: BaseException, code: str) -> None:
    assert tool_error_code(exc) == code
    assert code in TOOL_ERROR_CODES


def test_eu_1_a_cycle_in_the_cause_chain_ends() -> None:
    first, second = ValueError("a"), ValueError("b")
    first.__cause__, second.__cause__ = second, first
    assert tool_error_code(first) == "failed"


def make_call(invocation: str) -> EffectCall:
    effect = ToolEffect(
        tool_id=tool_id_for("lookup"),
        effect="read",
        idempotent=True,
        sink=SinkDescriptor(egress="external", args_leave_boundary=True),
    )
    return EffectCall("run-1", invocation, ToolCall(id=invocation, name="lookup", arguments={}), effect, args_digest({}))


async def test_eu_2_a_raising_read_keeps_code_and_masked_detail_apart() -> None:
    def failing(_call: object, _ctx: object) -> ToolExecution:
        raise ConnectionRefusedError(111, f"connect to slack failed, token={TOKEN}")

    async def tool(call: object, ctx: object) -> ToolExecution:
        return failing(call, ctx)

    call = make_call("inv-1")
    result = await EffectRunner(RequestEffectLog()).run(call, context(call), tool)
    execution = result.execution
    assert execution is not None
    assert execution.status == "error"
    assert execution.error_code == "unavailable"
    assert isinstance(execution.content, str)
    assert "ConnectionRefusedError" in execution.content
    assert TOKEN not in execution.content


def test_eu_2_the_result_document_keeps_the_code_beside_the_content() -> None:
    doc = json.loads(encode_result("ConnectError: refused", is_error=True, error_code="unavailable"))
    assert doc == {"content": "ConnectError: refused", "isError": True, "errorCode": "unavailable"}
    assert "errorCode" not in json.loads(encode_result("fine", is_error=False, error_code="unavailable"))


async def test_eu_2_an_error_the_tool_returned_without_a_code_reads_failed() -> None:
    async def tool(call: object, ctx: object) -> ToolExecution:
        return ToolExecution(status="error", content="the API said no")

    async def slow(call: object, ctx: object) -> ToolExecution:
        return ToolExecution(status="timeout", content="took too long")

    for fn, code in ((tool, "failed"), (slow, "timeout")):
        call = make_call(f"inv-{code}")
        result = await EffectRunner(RequestEffectLog()).run(call, context(call), fn)
        assert result.execution is not None
        assert result.execution.error_code == code
