"""`failure_reason` and `provider_code`: why a call failed, for frames, tool results, run results and traces, with
none of the error's text (a provider's answer can quote the prompt, an email or a key)."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.utils.error_class import failure_reason, provider_code
from tests.support.provider_errors import (
    SECRET,
    SENTINELS,
    httpx_error,
    openai_error,
    transport_error,
    with_cause,
)


def _body(code: str | None = None, type_: str = "invalid_request_error") -> dict:
    return {"message": f"MARK {SENTINELS[0]} jane.doe@example.com {SECRET}", "type": type_, "param": None, "code": code}


def _text_free(reason: str) -> None:
    assert not [s for s in (*SENTINELS, "F30SECRET", "MARK") if s in reason], reason


@pytest.mark.parametrize(("exc", "expected"), [
    (openai_error(400, _body("content_filter")), "bad_request: content_filter (BadRequestError, HTTP 400)"),
    (openai_error(401, _body()), "misconfigured: invalid_request_error (AuthenticationError, HTTP 401)"),
    (openai_error(429, _body("rate_limit_exceeded")), "rate_limited: rate_limit_exceeded (RateLimitError, HTTP 429)"),
    (openai_error(500, _body(type_="server_error")), "upstream_5xx: server_error (InternalServerError, HTTP 500)"),
    (httpx_error(422), "bad_request (HTTPStatusError, HTTP 422)"),
    (RuntimeError(SECRET), "internal (RuntimeError)"),
])
def test_the_reason_is_class_code_type_and_status(exc: BaseException, expected: str) -> None:
    assert failure_reason(exc) == expected
    _text_free(failure_reason(exc))


def test_through_the_transport_the_type_is_the_providers_and_the_status_the_answer() -> None:
    reason = failure_reason(transport_error(400, _body("context_length_exceeded")))
    assert reason == "bad_request: context_length_exceeded (BadRequestError, HTTP 400)"


def test_an_explicit_class_wins() -> None:
    assert failure_reason(transport_error(401), "misconfigured").startswith("misconfigured")


def test_no_status_means_no_http_part() -> None:
    assert failure_reason(TransportError(f"stream cut: {SECRET}", retryable=True)) == "unavailable (TransportError)"


def test_a_group_names_its_first_member() -> None:
    assert failure_reason(ExceptionGroup("tasks", [ValueError(SECRET)])) == "internal (ValueError)"


def test_an_sdk_property_that_raises_still_gives_a_reason() -> None:
    class Broken(Exception):
        @property
        def status_code(self) -> int:
            raise RuntimeError("property blew up")

    assert failure_reason(Broken(SECRET)) == "internal (Broken)"


def test_the_transports_own_code_is_read() -> None:
    err = TransportError(f"Provider content_filter stopped the response: {SECRET}", code="content_filter")
    assert provider_code(err) == "content_filter"


@pytest.mark.parametrize("code", ["content_filter", "context_length_exceeded", "DeploymentNotFound", "overloaded_error"])
def test_plain_word_codes_are_kept(code: str) -> None:
    assert provider_code(openai_error(400, _body(code))) == code


@pytest.mark.parametrize("value", [
    "org-AbC12345",                                   # an account id
    "a" * 8 + "0123456789abcdef0123456789abcdef",     # a hex key
    "ya29.a0AfH6SMBx",                                # a Google access token
    "req_123abc",                                     # a request id
    "a.b",
    "x" * 65,
    "has space",
    SECRET,
])
def test_a_server_written_code_that_is_not_a_plain_word_is_other(value: str) -> None:
    assert provider_code(openai_error(400, _body(value))) == "other"
    _text_free(failure_reason(openai_error(400, _body(value))))


def test_a_code_property_that_raises_is_skipped_not_raised() -> None:
    class Broken(Exception):
        @property
        def code(self) -> str:
            raise RuntimeError("property blew up")

        type = "overloaded_error"

    assert provider_code(Broken(SECRET)) == "overloaded_error"
    assert provider_code(with_cause(Broken(SECRET), openai_error(400, _body("content_filter")))) == "overloaded_error"

    class AllBroken(Exception):
        @property
        def code(self) -> str:
            raise RuntimeError("property blew up")

    assert provider_code(AllBroken(SECRET)) is None


def test_no_code_anywhere_is_none() -> None:
    assert provider_code(with_cause(RuntimeError("x"), ValueError("y"))) is None
