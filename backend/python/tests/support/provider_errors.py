"""Model provider errors as the SDKs raise them, and the check that a log line holds none of their text.

A provider puts its HTTP response body in the exception message, and the body can echo the prompt, the
document or the account's email. ``MARK`` stands for that text: tests put it in the body and assert it
reaches no rendered log line, in the text format or the JSON one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import openai
from pydantic import BaseModel, ValidationError

from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterable

MARK = "F24BMARK jane.doe@example.com"

_OPENAI_ERRORS: dict[int, type[openai.APIStatusError]] = {
    400: openai.BadRequestError,
    401: openai.AuthenticationError,
    404: openai.NotFoundError,
    429: openai.RateLimitError,
    500: openai.InternalServerError,
}
_REQUEST = httpx.Request("POST", "https://api.example/v1/chat/completions")


def openai_error(status: int, error: dict | None = None) -> openai.APIStatusError:
    """The openai SDK's error for ``status``, its message in the SDK's own format (``Error code: N - {body}``)."""
    body = {"error": error or {"message": MARK, "type": "invalid_request_error", "param": None, "code": None}}
    response = httpx.Response(status, request=_REQUEST, headers={"x-request-id": "req_f24b"})
    return _OPENAI_ERRORS[status](f"Error code: {status} - {body}", response=response, body=body)


def httpx_error(status: int, message: str = MARK) -> httpx.HTTPStatusError:
    """A provider without its own SDK error (Mistral through httpx): the body is in the message."""
    response = httpx.Response(status, request=_REQUEST, text=message)
    return httpx.HTTPStatusError(f"Client error '{status}' for url '{_REQUEST.url}': {message}",
                                 request=_REQUEST, response=response)


def assert_text_free(records: Iterable[logging.LogRecord]) -> None:
    """No record, rendered as text or JSON, carries MARK or an email, and none carries error text."""
    for record in records:
        for formatter in (TextFormatter(), JsonFormatter()):
            line = formatter.format(record)
            assert "F24BMARK" not in line, line
            assert "@example.com" not in line, line
        assert log_record_fields(record).get("error_message") is None, record.getMessage()


def failure_line(records: Iterable[logging.LogRecord], event: str, error_class: str, level: int) -> dict[str, object]:
    """The one ``event`` line, checked as a text-free failure of ``error_class`` at ``level``; its fields."""
    lines = [r for r in records if r.getMessage() == event]
    assert len(lines) == 1, [r.getMessage() for r in records]
    fields = log_record_fields(lines[0])
    assert (fields.get("outcome"), fields.get("error_class"), lines[0].levelno) == ("failure", error_class, level)
    assert lines[0].exc_info is None
    assert_text_free(lines)
    return fields


def with_cause(error: Exception, cause: BaseException) -> Exception:
    """``error`` as if raised ``from cause``: how a wrapper carries the provider's error up."""
    error.__cause__ = cause
    return error


def schema_mismatch(model: type[BaseModel]) -> ValidationError:
    """The error pydantic raises for a model answer that does not fit ``model``; the answer quotes MARK."""
    try:
        model.model_validate_json(f'{{"unexpected": "{MARK}"}}')
    except ValidationError as exc:
        return exc
    raise AssertionError(f"an empty answer fit {model.__name__}")
