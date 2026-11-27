"""FIX-R2 B4: a provider error the durable model port could not classify failed the run as ``unknown``.

Two native runs were lost this way (``model.failed errorType=unknown``, the first loop call,
12-16 s into the stream, in both processes within six minutes). The port classified by the error's text alone, so
a failure that says what it is only through its type, status or body read as unknown: the provider's stream
reporting a server error after it accepted the request (``openai.APIError``, no HTTP status, the message carries no
code), a connection the provider or a gateway dropped mid-stream (``httpx.RemoteProtocolError`` "peer closed
connection ..."), a read that failed with no message, an overloaded 529. Each is transient: the kernel retries a
``server_error`` or ``timeout`` on the route and, when the retries run out, the answer says the provider is not
responding, never "something went wrong".
"""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest

from app.agents.agent_loop.error_classification import (
    classify_model_exception,
    model_call_failure_message,
)
from app.harness.assembly.model import ClassifiedModelPort
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.durable.ports import ModelBackoff, ModelFailed
from harness_core.models.policy import RETRIED, classify_failure
from harness_core.testing import ScriptedModel
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import World, reply, script

_REQUEST = httpx.Request("POST", "https://contoso.openai.azure.com/openai/v1/chat/completions")
_AZURE_STREAM_ERROR = (
    "The server had an error processing your request. Sorry about that! You can retry your request, or contact us "
    "through our help center at help.openai.com if you keep seeing this error."
)


def _wrapped(exc: Exception, *, status_code: int | None = None, retryable: bool = False) -> TransportError:
    """``exc`` as ``LangChainTransport._wrap_error`` raises it: the class name and text, the status, the cause."""
    error = TransportError(
        f"LangChain transport error (stream): {type(exc).__name__}: {exc}",
        status_code=status_code,
        retryable=retryable,
    )
    error.__cause__ = exc
    return error


def _stream_error_event(body: dict[str, Any] | None) -> openai.APIError:
    """What openai's ``Stream`` raises for an ``error`` event in an accepted stream: no status, the body's message."""
    message = (body or {}).get("message") or "An error occurred during streaming"
    return openai.APIError(message, _REQUEST, body=body)


def _status_error(status: int, text: str) -> openai.APIStatusError:
    response = httpx.Response(status, request=_REQUEST, json={"error": {"message": text}})
    return openai.APIStatusError(text, response=response, body={"message": text})


TRANSIENT: list[tuple[str, Exception, str]] = [
    (
        "azure server error inside an accepted stream",
        _wrapped(_stream_error_event({"message": _AZURE_STREAM_ERROR, "type": "server_error", "code": None})),
        "server_error",
    ),
    (
        "a stream error event with no message",
        _wrapped(_stream_error_event({"code": None})),
        "server_error",
    ),
    (
        # Retryable as false: deltas had reached the client, which matters to the legacy loop's whole-call retry,
        # not to the durable kernel, whose retry is a new model attempt.
        "the connection dropped mid-stream",
        _wrapped(
            httpx.RemoteProtocolError("peer closed connection without sending complete message body (incomplete chunked read)"),
            retryable=False,
        ),
        "server_error",
    ),
    ("a read that failed with no message", _wrapped(httpx.ReadError(""), retryable=True), "server_error"),
    ("an overloaded provider (529)", _wrapped(_status_error(529, "Overloaded"), status_code=529, retryable=True), "server_error"),
    ("a request timeout status (408)", _wrapped(_status_error(408, "Request Timeout"), status_code=408), "timeout"),
    ("a bare timeout", _wrapped(TimeoutError()), "timeout"),
]


@pytest.mark.parametrize(("exc", "code"), [(c[1], c[2]) for c in TRANSIENT], ids=[c[0] for c in TRANSIENT])
def test_evalfix2_b4_a_transient_provider_error_is_classified_from_what_it_is(exc: Exception, code: str) -> None:
    assert classify_model_exception(exc) == code


@pytest.mark.parametrize("exc", [c[1] for c in TRANSIENT], ids=[c[0] for c in TRANSIENT])
async def test_evalfix2_b4_the_port_fails_the_call_as_a_failure_the_kernel_retries(exc: Exception) -> None:
    class Inner:
        async def sample(self, call: object, on_delta: object) -> Any:  # noqa: ANN401
            raise exc

    with pytest.raises(ModelFailed) as raised:
        _ = await ClassifiedModelPort(Inner()).sample(object(), object())  # type: ignore[arg-type]
    assert raised.value.error_type != "unknown"
    assert classify_failure(raised.value) in RETRIED
    assert model_call_failure_message(raised.value.error_type) != model_call_failure_message("unknown")


TERMINAL: list[tuple[str, Exception, str]] = [
    (
        "a 400 the provider explains",
        _wrapped(_status_error(400, "Error code: 400 - invalid_request_error: tools[3] is invalid"), status_code=400),
        "invalid_request",
    ),
    (
        "a filtered prompt inside the stream",
        _wrapped(_stream_error_event({"message": "blocked", "code": "content_filter"})),
        "content_filter",
    ),
    ("a rejected key", _wrapped(_status_error(401, "Error code: 401 - unauthorized"), status_code=401), "auth_error"),
    (
        # OpenAI's Responses stream for an account out of credits: the body's type says it, the message does not.
        "an exhausted account inside the stream",
        _wrapped(
            _stream_error_event(
                {
                    "message": "You have no credits remaining. Add credits to continue using the API.",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            )
        ),
        "quota_exceeded",
    ),
]


async def test_an_exhausted_account_inside_the_stream_fails_the_call_without_a_retry() -> None:
    class Inner:
        async def sample(self, call: object, on_delta: object) -> Any:  # noqa: ANN401
            raise TERMINAL[-1][1]

    with pytest.raises(ModelFailed) as raised:
        _ = await ClassifiedModelPort(Inner()).sample(object(), object())  # type: ignore[arg-type]
    assert raised.value.error_type == "quota_exceeded"
    assert classify_failure(raised.value) not in RETRIED


@pytest.mark.parametrize(("exc", "code"), [(c[1], c[2]) for c in TERMINAL], ids=[c[0] for c in TERMINAL])
def test_evalfix2_b4_a_terminal_provider_error_keeps_its_clear_class(exc: Exception, code: str) -> None:
    assert classify_model_exception(exc) == code


def test_evalfix2_b4_a_failure_that_says_nothing_about_the_provider_stays_unknown() -> None:
    # A bug in our own code is not a provider blip: retrying it would only repeat it.
    assert classify_model_exception(_wrapped(KeyError("choices"))) == "unknown"
    assert classify_model_exception(ValueError("boom")) == "unknown"


async def test_evalfix2_b4_a_rate_limit_without_a_status_in_the_text_still_backs_off() -> None:
    class Inner:
        async def sample(self, call: object, on_delta: object) -> Any:  # noqa: ANN401
            raise _wrapped(_status_error(429, "Slow down"), status_code=429, retryable=True)

    with pytest.raises(ModelBackoff):
        _ = await ClassifiedModelPort(Inner()).sample(object(), object())  # type: ignore[arg-type]


class _DropsOnce:
    """The first call fails as the confirmation run's did; later calls answer."""

    def __init__(self, inner: ScriptedModelPort, error: Exception) -> None:
        self._inner = inner
        self._error: Exception | None = error
        self.calls = 0

    async def sample(self, call: Any, on_delta: Any) -> Any:  # noqa: ANN401
        self.calls += 1
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        return await self._inner.sample(call, on_delta)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:  # noqa: ANN401
        return self._inner.restore(raw, provider=provider, transport=transport)


async def test_evalfix2_b4_a_run_whose_stream_reports_a_server_error_retries_and_completes() -> None:
    world = World()
    world.admit(spec=minimal_run_spec())
    inner = _DropsOnce(
        ScriptedModelPort(ScriptedModel(script(reply("fine")))),
        _wrapped(_stream_error_event({"message": _AZURE_STREAM_ERROR, "type": "server_error"})),
    )
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    done = await world.run(world.kernel(ClassifiedModelPort(inner), sleep=sleep))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert (inner.calls, slept) == (2, [1.0])
    assert world.events("run.failed") == []
