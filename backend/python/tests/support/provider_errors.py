"""Model provider errors as the SDKs raise them.

A provider puts its HTTP response body in the exception message, and the body can echo the prompt, the
document or the account's email. ``MARK`` stands for that text: tests put it in the body and assert, with
``tests/support/log_capture.py``, that none of ``SENTINELS`` reaches a log line.
"""

from __future__ import annotations

import httpx
import openai
from pydantic import BaseModel, ValidationError

from app.agent_loop_lib.core.exceptions import TransportError

MARK = "F24BMARK jane.doe@example.com"
SENTINELS = ("F24BMARK", "@example.com")  # MARK, and any test account's email
SECRET = "sk-proj-F30SECRETabcdefghijklmnopqrstuv"  # a key-shaped secret a body can carry; the masker knows the shape

_OPENAI_ERRORS: dict[int, type[openai.APIStatusError]] = {
    400: openai.BadRequestError,
    401: openai.AuthenticationError,
    404: openai.NotFoundError,
    429: openai.RateLimitError,
    500: openai.InternalServerError,
}
_REQUEST = httpx.Request("POST", "https://api.example/v1/chat/completions")


def openai_error(status: int, error: dict | None = None) -> openai.APIStatusError:
    """The openai SDK's error for ``status``, its message in the SDK's own format (``Error code: N - {body}``) and,
    as the SDK does, the inner ``error`` object as ``body`` (so ``.code`` and ``.type`` are set from it)."""
    body = {"error": error or {"message": MARK, "type": "invalid_request_error", "param": None, "code": None}}
    response = httpx.Response(status, request=_REQUEST, headers={"x-request-id": "req_f24b"})
    return _OPENAI_ERRORS[status](f"Error code: {status} - {body}", response=response, body=body["error"])


def transport_error(status: int, error: dict | None = None) -> TransportError:
    """What the agent loop's LangChain transport raises for a provider's answer: the SDK's error, quoted, and raised
    ``from`` it (``LangChainTransport._wrap_error``)."""
    sdk = openai_error(status, error)
    return with_cause(TransportError(f"LangChain transport error (complete): {type(sdk).__name__}: {sdk}",
                                     status_code=status), sdk)


def httpx_error(status: int, message: str = MARK) -> httpx.HTTPStatusError:
    """A provider without its own SDK error (Mistral through httpx): the body is in the message."""
    response = httpx.Response(status, request=_REQUEST, text=message)
    return httpx.HTTPStatusError(f"Client error '{status}' for url '{_REQUEST.url}': {message}",
                                 request=_REQUEST, response=response)


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
