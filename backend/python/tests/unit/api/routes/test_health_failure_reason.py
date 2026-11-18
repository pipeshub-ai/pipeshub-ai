"""A failed model check tells the admin why: the provider's own words reach the dialog, masked."""

import json

import httpx
import openai
from fastapi import HTTPException

from app.api.routes.health import _http_exception_response, _model_setup_failed_response

_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
_CONFIG = {"provider": "openAI", "configuration": {"model": "gpt-6-luna", "apiKey": _KEY}}
_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _openai_error(cls: type[openai.APIStatusError], status: int, message: str) -> openai.APIStatusError:
    return cls(
        message,
        response=httpx.Response(status, request=_REQUEST),
        body={"error": {"message": message, "type": "error", "code": None}},
    )


async def _check(error: BaseException) -> tuple[int, dict]:
    response = await _model_setup_failed_response("chat model", _CONFIG, "gpt-6-luna", error)  # type: ignore[arg-type]
    return response.status_code, json.loads(response.body)


async def test_exhausted_quota_says_quota_and_quotes_openai() -> None:
    quota = "You exceeded your current quota, please check your plan and billing details."
    status, body = await _check(_openai_error(openai.RateLimitError, 429, quota))
    assert status == 400
    assert body["details"]["error_code"] == "rate_limit"
    assert "exhausted quota" in body["message"]
    assert "API key" not in body["message"]
    assert body["message"].endswith(f'OpenAI said: "{quota}"')


async def test_provider_server_error_is_a_502_with_the_provider_reason() -> None:
    status, body = await _check(_openai_error(openai.InternalServerError, 500, "The server had an error."))
    assert status == 502
    assert body["details"]["error_code"] == "provider_error"
    assert body["message"].endswith('OpenAI said: "The server had an error."')


async def test_rejected_settings_keep_their_advice_and_add_the_provider_reason() -> None:
    reason = "The model `gpt-6-luna` does not exist or you do not have access to it."
    status, body = await _check(_openai_error(openai.NotFoundError, 404, reason))
    assert status == 400
    assert "couldn't find the model" in body["message"]
    assert body["message"].endswith(f'OpenAI said: "{reason}"')


async def test_unclassified_failure_keeps_the_fixed_message_and_adds_the_reason() -> None:
    reason = "This model is only supported in v1/responses and not in v1/chat/completions."
    status, body = await _check(_openai_error(openai.UnprocessableEntityError, 422, reason))
    assert status == 500
    assert body["message"].startswith("Couldn't connect to OpenAI with these chat model settings.")
    assert body["message"].endswith(f'OpenAI said: "{reason}"')


async def test_the_configured_key_and_urls_never_reach_the_dialog() -> None:
    leaky = f"Incorrect API key provided: {_KEY}. See https://platform.openai.com/account/api-keys?key={_KEY}"
    _, body = await _check(_openai_error(openai.AuthenticationError, 401, leaky))
    assert _KEY not in body["message"]
    assert "platform.openai.com" not in body["message"]
    assert "Incorrect API key provided" in body["message"]


async def test_a_long_provider_message_is_capped() -> None:
    _, body = await _check(_openai_error(openai.InternalServerError, 500, "overloaded " * 200))
    reason = body["message"].split('OpenAI said: "', 1)[1].rstrip('"')
    assert len(reason) <= 300
    assert reason.endswith("…")


class _InternalFailure(Exception):
    pass


_InternalFailure.__module__ = "app.services.example"


async def test_pipeshub_own_exception_text_stays_out_of_the_response() -> None:
    _, body = await _check(_InternalFailure("connection to neo4j://graph-db:7687 refused"))
    assert "neo4j" not in body["message"]
    assert "said:" not in body["message"]


async def test_the_provider_cause_under_a_pipeshub_wrapper_is_quoted() -> None:
    cause = _openai_error(openai.RateLimitError, 429, "Rate limit reached for requests.")
    try:
        try:
            raise cause
        except openai.RateLimitError as exc:
            raise _InternalFailure("model call failed") from exc
    except _InternalFailure as wrapped:
        _, body = await _check(wrapped)
    assert body["message"].endswith('OpenAI said: "Rate limit reached for requests."')
    assert "model call failed" not in body["message"]


def test_plain_text_http_exception_detail_becomes_a_message() -> None:
    response = _http_exception_response(HTTPException(status_code=500, detail="No default embedding model found"))
    assert response.status_code == 500
    assert json.loads(response.body) == {"status": "error", "message": "No default embedding model found"}


def test_structured_http_exception_detail_is_sent_as_is() -> None:
    detail = {"status": "not healthy", "error": "The embedding model couldn't start."}
    response = _http_exception_response(HTTPException(status_code=500, detail=detail))
    assert json.loads(response.body) == detail


async def _check_as(provider: str, error: BaseException) -> dict:
    config = {**_CONFIG, "provider": provider}
    response = await _model_setup_failed_response("chat model", config, "gpt-6-luna", error)  # type: ignore[arg-type]
    return json.loads(response.body)


async def test_a_self_hosted_endpoint_answer_stays_in_the_log() -> None:
    error = _openai_error(openai.RateLimitError, 429, "vLLM queue full on worker-3")
    for provider in ("openAICompatible", "litellmProxy", "ollama"):
        body = await _check_as(provider, error)
        assert "said:" not in body["message"]
        assert "worker-3" not in body["message"]


async def test_an_error_no_provider_sdk_raised_stays_in_the_log() -> None:
    _, body = await _check(RuntimeError("cannot schedule new futures after shutdown"))
    assert "said:" not in body["message"]


async def test_provider_text_naming_internals_is_dropped() -> None:
    for leaked in (
        "Traceback (most recent call last): boom",
        'File "/srv/app/main.py", line 3',
        "upstream gpt-gateway.svc refused",
        "proxy at 10.0.4.17:8080 refused the call",
        "connect to localhost failed",
    ):
        _, body = await _check(_openai_error(openai.InternalServerError, 500, leaked))
        assert "said:" not in body["message"], leaked
