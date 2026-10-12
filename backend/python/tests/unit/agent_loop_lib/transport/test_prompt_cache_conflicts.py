"""``is_prompt_cache_param_conflict`` against the errors the real SDKs raise."""

from __future__ import annotations

import anthropic
import httpx
import openai
import pytest

from app.agent_loop_lib.transport.provider_conflicts import (
    is_prompt_cache_param_conflict,
)

SENT = {"prompt_cache_key", "prompt_cache_retention"}


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.example/v1/chat/completions"))


@pytest.mark.parametrize("message", [
    "Error code: 400 - Unrecognized request argument supplied: prompt_cache_retention",
    "Error code: 400 - {'error': {'message': \"Unknown parameter: 'prompt_cache_key'.\"}}",
])
def test_an_openai_400_naming_a_sent_parameter(message: str) -> None:
    assert is_prompt_cache_param_conflict(openai.BadRequestError(message, response=_response(400), body=None), SENT)


def test_an_anthropic_400_naming_cache_control() -> None:
    exc = anthropic.BadRequestError(
        "Error code: 400 - cache_control: Extra inputs are not permitted", response=_response(400), body=None,
    )
    assert is_prompt_cache_param_conflict(exc, {"cache_control"})


def _bedrock_error(status: int, message: str) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": "ValidationException", "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "Converse",
    )


def test_a_bedrock_validation_error_naming_cache_point() -> None:
    exc = _bedrock_error(400, "The model returned the following errors: cachePoint is not supported for this model")
    assert is_prompt_cache_param_conflict(exc, {"cachepoint"})


def test_a_bedrock_throttle_is_not_a_cache_conflict() -> None:
    assert not is_prompt_cache_param_conflict(_bedrock_error(429, "Too many requests with cachePoint"), {"cachepoint"})


@pytest.mark.parametrize(("exc", "sent"), [
    (openai.BadRequestError("Error code: 400 - Invalid 'messages[3]'", response=_response(400), body=None), SENT),
    (openai.BadRequestError("Error code: 400 - prompt_cache_key is invalid", response=_response(400), body=None), set()),
    (openai.RateLimitError("Error code: 429 - prompt_cache_key over limit", response=_response(429), body=None), SENT),
    (RuntimeError("connection reset while sending prompt_cache_key"), SENT),
])
def test_anything_else_is_not_a_cache_conflict(exc: Exception, sent: set[str]) -> None:
    assert not is_prompt_cache_param_conflict(exc, sent)
