"""A model check the provider refuses because of the admin's settings is a 4xx, not a 500."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from fastapi.responses import JSONResponse

from app.api.routes.health import (
    embedding_health_check,
    perform_embedding_health_check,
    perform_llm_health_check,
)

MODULE = "app.api.routes.health"
_REQUEST = httpx.Request("POST", "https://example.openai.azure.com/openai/deployments/gpt/chat/completions")


def _status_error(cls: type[openai.APIStatusError], status: int, body: dict) -> openai.APIStatusError:
    return cls(f"Error code: {status} - {body}", response=httpx.Response(status, request=_REQUEST), body=body)


def _connection_error() -> openai.APIConnectionError:
    try:
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=_REQUEST)
    except httpx.ConnectError as cause:
        try:
            raise openai.APIConnectionError(request=_REQUEST) from cause
        except openai.APIConnectionError as wrapped:
            return wrapped


def _llm_config() -> dict:
    return {
        "provider": "azureOpenAI",
        "configuration": {"model": "gpt-4o", "apiKey": "wrong", "modelFriendlyName": "Team GPT"},
    }


async def _check_llm(error: Exception, *, egress: bool = True) -> tuple[JSONResponse, dict]:
    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=error)
    with patch(f"{MODULE}.get_generator_model", return_value=model), \
         patch(f"{MODULE}._probe_outbound_connectivity", new_callable=AsyncMock, return_value=egress):
        resp = await perform_llm_health_check(_llm_config(), MagicMock())
    return resp, json.loads(resp.body)


@pytest.mark.asyncio
async def test_a_rejected_key_is_a_400_that_names_the_model_and_provider() -> None:
    error = _status_error(openai.AuthenticationError, 401, {"error": {"code": "401", "message": "Access denied"}})

    resp, body = await _check_llm(error)

    assert resp.status_code == 400
    assert body["message"] == (
        "The API key for Team GPT was rejected by Azure OpenAI. "
        "Check the key in Workspace > AI Models, then try again."
    )
    assert body["details"]["error_code"] == "auth_error"


@pytest.mark.asyncio
async def test_a_forbidden_key_is_a_settings_error_too() -> None:
    error = _status_error(openai.PermissionDeniedError, 403, {"error": {"message": "Forbidden"}})

    resp, body = await _check_llm(error)

    assert resp.status_code == 400
    assert "API key" in body["message"]


@pytest.mark.asyncio
async def test_a_missing_model_is_a_400_that_names_the_model() -> None:
    error = _status_error(openai.NotFoundError, 404, {"error": {"code": "DeploymentNotFound", "message": "gone"}})

    resp, body = await _check_llm(error)

    assert resp.status_code == 400
    assert '"gpt-4o"' in body["message"] and "model name" in body["message"]
    assert body["details"]["error_code"] == "model_not_found"


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_is_a_400_when_the_internet_is_reachable() -> None:
    resp, body = await _check_llm(_connection_error())

    assert resp.status_code == 400
    assert "endpoint" in body["message"]
    assert body["details"]["error_code"] == "endpoint_unreachable"


@pytest.mark.asyncio
async def test_no_internet_at_all_stays_a_server_side_problem() -> None:
    resp, body = await _check_llm(_connection_error(), egress=False)

    assert resp.status_code == 500
    assert body["details"]["error_code"] == "outbound_connectivity"


@pytest.mark.asyncio
async def test_an_error_that_is_not_the_settings_stays_a_500() -> None:
    resp, _ = await _check_llm(RuntimeError("boom"))

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_an_embedding_model_with_a_rejected_key_is_a_400() -> None:
    config = {"provider": "openAI", "configuration": {"model": "text-embedding-3-small", "apiKey": "wrong"}}
    error = _status_error(openai.AuthenticationError, 401, {"error": {"message": "Incorrect API key provided"}})

    with patch(f"{MODULE}.get_embedding_model", side_effect=error):
        resp = await perform_embedding_health_check(MagicMock(), config, MagicMock())

    assert resp.status_code == 400
    assert "rejected by OpenAI" in json.loads(resp.body)["message"]


@pytest.mark.asyncio
async def test_a_missing_local_file_is_not_called_an_unreachable_endpoint() -> None:
    resp, body = await _check_llm(FileNotFoundError("model weights not found"))

    assert resp.status_code == 500
    assert not body["message"].startswith("Couldn't reach")


@pytest.mark.asyncio
async def test_provider_and_exception_text_never_reach_the_response() -> None:
    leaked = "Traceback (most recent call last): secret-host.internal req-9f2"
    for error in (
        _status_error(openai.BadRequestError, 400, {"error": {"message": leaked}}),
        RuntimeError(leaked),
        _status_error(openai.AuthenticationError, 401, {"error": {"message": leaked}}),
    ):
        resp, body = await _check_llm(error)
        assert leaked not in resp.body.decode()
        assert "error_type" not in body["details"]


def _embedding_request(embed_error: Exception) -> tuple[MagicMock, MagicMock]:
    retrieval_service = MagicMock()
    request = MagicMock()
    request.app.container.retrieval_service = AsyncMock(return_value=retrieval_service)
    embeddings = MagicMock()
    embeddings.aembed_query = AsyncMock(side_effect=embed_error)
    return request, embeddings


@pytest.mark.asyncio
async def test_set_default_embedding_with_a_rejected_key_is_a_400() -> None:
    config = {
        "provider": "openAI", "isDefault": True,
        "configuration": {"model": "text-embedding-3-small", "apiKey": "wrong"},
    }
    error = _status_error(openai.AuthenticationError, 401, {"error": {"message": "Incorrect API key provided"}})
    request, embeddings = _embedding_request(error)

    with patch(f"{MODULE}.get_embedding_model", return_value=embeddings):
        resp = await embedding_health_check(request, [config])

    body = json.loads(resp.body)
    assert resp.status_code == 400
    assert body["details"]["error_code"] == "auth_error"
    assert "rejected by OpenAI" in body["message"]


@pytest.mark.asyncio
async def test_set_default_embedding_that_cannot_be_built_for_a_missing_model_is_a_400() -> None:
    config = {"provider": "openAI", "configuration": {"model": "gone", "apiKey": "k"}}
    error = _status_error(openai.NotFoundError, 404, {"error": {"message": "model gone"}})
    request, _ = _embedding_request(error)

    with patch(f"{MODULE}.get_embedding_model", side_effect=error):
        resp = await embedding_health_check(request, [config])

    assert resp.status_code == 400
    assert json.loads(resp.body)["details"]["error_code"] == "model_not_found"


@pytest.mark.asyncio
async def test_set_default_embedding_other_failures_stay_a_500_without_their_text() -> None:
    request, embeddings = _embedding_request(RuntimeError("boom at secret-host"))

    with patch(f"{MODULE}.get_embedding_model", return_value=embeddings):
        resp = await embedding_health_check(request, [{"provider": "openAI", "configuration": {"model": "m"}}])

    assert resp.status_code == 500
    assert "secret-host" not in resp.body.decode()
