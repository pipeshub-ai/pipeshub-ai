"""A model saved with its API key empty is the admin's to fix, so the health
check answers 400 naming the field, before any provider is called.

The provider SDKs refuse a blank key before sending anything ("Missing
credentials"), and that error used to come back as a 500 "Couldn't connect"
that read as PipesHub's fault. Clearing the key while editing a model sends "".
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes import health


def _request() -> MagicMock:
    request = MagicMock()
    request.app.container.logger.return_value = MagicMock()
    return request


def _azure(api_key: object = "real-key", *, drop_key: bool = False) -> dict:
    configuration = {
        "model": "gpt-4o",
        "deploymentName": "gpt-4o",
        "endpoint": "https://contoso.openai.azure.com/",
        "apiKey": api_key,
    }
    if drop_key:
        del configuration["apiKey"]
    return {"provider": "azureOpenAI", "configuration": configuration}


@pytest.mark.parametrize(
    ("api_key", "drop_key"),
    [("", False), ("   ", False), (None, False), (None, True)],
    ids=["empty", "blank", "null", "absent"],
)
async def test_an_empty_api_key_is_a_400_naming_the_field_and_no_provider_is_called(
    api_key: object, drop_key: bool,
) -> None:
    with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
        response = await health.health_check(_request(), "llm", _azure(api_key, drop_key=drop_key))

    body = json.loads(response.body)
    assert response.status_code == 400
    assert body["message"] == (
        "The API Key for Azure OpenAI is empty. Enter it in Workspace > AI Models, then try again."
    )
    assert body["details"]["error_code"] == "missing_credentials"
    perform.assert_not_awaited()


async def test_a_key_that_is_set_is_checked_as_usual() -> None:
    with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock, return_value="checked"):
        assert await health.health_check(_request(), "llm", _azure("real-key")) == "checked"


@pytest.mark.parametrize("model_type", ["llm", "embedding"])
async def test_a_provider_that_needs_no_key_is_checked_without_one(model_type: str) -> None:
    performer = "perform_llm_health_check" if model_type == "llm" else "perform_embedding_health_check"
    config = {"provider": "ollama", "configuration": {"model": "llama3", "endpoint": "http://localhost:11434"}}
    with patch.object(health, performer, new_callable=AsyncMock, return_value="checked"):
        assert await health.health_check(_request(), model_type, config) == "checked"


async def test_the_key_a_model_type_requires_is_read_for_that_type() -> None:
    """OpenAI-compatible endpoints need a key for chat but not for speech."""
    config = {"provider": "openAICompatible", "configuration": {"model": "m", "endpoint": "https://api.example/v1"}}
    with patch.object(health, "perform_tts_health_check", new_callable=AsyncMock, return_value="checked"):
        assert await health.health_check(_request(), "tts", config) == "checked"
    with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
        response = await health.health_check(_request(), "llm", config)
    assert response.status_code == 400
    perform.assert_not_awaited()


async def test_a_missing_service_account_file_is_named_too() -> None:
    config = {"provider": "vertexAI", "configuration": {"model": "gemini-2.0-flash", "serviceAccountJson": ""}}
    with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
        response = await health.health_check(_request(), "llm", config)

    assert response.status_code == 400
    assert json.loads(response.body)["details"]["error_code"] == "missing_credentials"
    perform.assert_not_awaited()


async def test_an_unknown_provider_is_left_to_the_check() -> None:
    config = {"provider": "notARealProvider", "configuration": {"model": "m"}}
    with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock, return_value="checked"):
        assert await health.health_check(_request(), "llm", config) == "checked"


async def test_the_bulk_routes_refuse_an_empty_key_the_same_way() -> None:
    with patch.object(health, "perform_llm_health_check", new_callable=AsyncMock) as perform:
        response = await health.llm_health_check(_request(), [_azure("")])
    assert response.status_code == 400
    assert json.loads(response.body)["details"]["error_code"] == "missing_credentials"
    perform.assert_not_awaited()

    with patch.object(health, "initialize_embedding_model", new_callable=AsyncMock) as initialize:
        response = await health.embedding_health_check(_request(), [_azure("")])
    assert response.status_code == 400
    assert json.loads(response.body)["details"]["error_code"] == "missing_credentials"
    initialize.assert_not_awaited()
