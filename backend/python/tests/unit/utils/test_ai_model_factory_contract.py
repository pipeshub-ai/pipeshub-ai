"""Registry fields must be enough to construct each provider factory.

A missing key here is a form field the UI never asked for, so saving a model
built from the registry would crash at runtime.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.config.ai_models.providers  # noqa: F401  -- populate the registry
from app.config.ai_models.registry import ai_model_registry
from app.utils.aimodels import (
    _reasoning_effort_kwargs,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
    iter_model_names,
    select_model_name,
)

_FACTORIES = {
    "text_generation": get_generator_model,
    "embedding": get_embedding_model,
    "image_generation": get_image_generation_model,
    "tts": get_tts_model,
    "stt": get_stt_model,
}


def _dummy_value(field: dict) -> object:
    name = field["name"]
    field_type = field.get("fieldType")
    if name in {"endpoint", "baseUrl"} or field_type == "URL":
        return "https://test.endpoint.com/v1"
    if name == "model":
        return "test-model"
    if name == "deploymentName":
        return "test-deployment"
    if field.get("isSecret"):
        return "test-key"
    options = field.get("options") or []
    if options:
        return options[0]["value"]
    default = field.get("defaultValue")
    if default not in (None, ""):
        return default
    if field_type == "NUMBER":
        return 128
    if field_type == "BOOLEAN":
        return False
    return "test-value"


def _cases():
    cases = []
    for provider in ai_model_registry.list_providers():
        pid = provider["providerId"]
        fields_by_cap = provider.get("fields") or {}
        for capability in provider.get("capabilities") or []:
            if capability not in _FACTORIES:
                continue
            config = {
                field["name"]: _dummy_value(field)
                for field in fields_by_cap.get(capability, [])
                if field.get("required")
            }
            if "model" not in config and provider.get("modelName"):
                config["model"] = provider["modelName"]
            if "model" not in config:
                config["model"] = "test-model"
            cases.append((pid, capability, config))
    return cases


@pytest.mark.parametrize("provider_id,capability,configuration", _cases())
def test_required_registry_fields_do_not_key_error(provider_id, capability, configuration):
    entry = {
        "provider": provider_id,
        "configuration": configuration,
        "isDefault": True,
        "isReasoning": False,
        "isMultimodal": False,
    }
    factory = _FACTORIES[capability]
    try:
        factory(provider_id, entry)
    except KeyError as exc:
        pytest.fail(
            f"{provider_id}/{capability} factory read missing key {exc}. "
            f"Required registry fields were {sorted(configuration)}"
        )
    except Exception:
        # SDK, credential, or endpoint failures are outside this contract.
        return


def test_iter_model_names_splits_legacy_lists_and_ignores_blanks():
    assert iter_model_names({"model": " gpt-4, gpt-4o , ,gpt-4o-mini "}) == [
        "gpt-4",
        "gpt-4o",
        "gpt-4o-mini",
    ]
    assert iter_model_names(None) == []
    assert iter_model_names({}) == []


def test_select_model_name_rejects_unknown_name_on_non_default():
    configuration = {"model": "a, b"}
    assert select_model_name(configuration, None) == "a"
    assert select_model_name(configuration, "other", is_default=True) == "other"
    with pytest.raises(ValueError, match="not found"):
        select_model_name(configuration, "other", is_default=False)


def test_openai_custom_base_url_does_not_use_responses_api():
    result = _reasoning_effort_kwargs(
        "high",
        {"isReasoning": True},
        provider="openAI",
        base_url="https://gateway.internal.example/v1",
        model_name="gpt-5",
    )
    assert result == {"reasoning_effort": "high"}


@patch("langchain_openai.embeddings.AzureOpenAIEmbeddings", return_value=MagicMock())
def test_azure_embedding_passes_deployment_name(mock_cls):
    get_embedding_model(
        "azureOpenAI",
        {
            "configuration": {
                "model": "text-embedding-3-small",
                "apiKey": "test-key",
                "endpoint": "https://test.endpoint.com",
                "deploymentName": "embed-deploy",
            },
            "isDefault": True,
        },
    )
    assert mock_cls.call_args.kwargs["azure_deployment"] == "embed-deploy"


@patch("langchain_openai.embeddings.OpenAIEmbeddings", return_value=MagicMock())
def test_openai_embedding_base_url_is_optional(mock_cls):
    get_embedding_model(
        "openAI",
        {
            "configuration": {
                "model": "text-embedding-3-small",
                "apiKey": "test-key",
                "endpoint": "https://test.endpoint.com/v1",
            },
            "isDefault": True,
        },
    )
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["base_url"] == "https://test.endpoint.com/v1"
    assert kwargs["check_embedding_ctx_length"] is False


def test_openai_compatible_media_adapters_use_configured_base_url():
    config = {
        "configuration": {
            "model": "custom-model",
            "endpoint": "https://test.endpoint.com/v1",
            "apiKey": "test-key",
            "voice": "alloy",
            "responseFormat": "wav",
        },
        "isDefault": True,
    }
    image = get_image_generation_model("openAICompatible", config)
    tts = get_tts_model("openAICompatible", config)
    stt = get_stt_model("openAICompatible", config)
    assert image.provider == "openAICompatible"
    assert image._base_url == "https://test.endpoint.com/v1"
    assert tts.provider == "openAICompatible"
    assert tts._base_url == "https://test.endpoint.com/v1"
    assert tts.default_format == "wav"
    assert stt.provider == "openAICompatible"
    assert stt._base_url == "https://test.endpoint.com/v1"


def test_openai_provider_declares_optional_base_url_and_organization():
    provider = ai_model_registry.get_provider("openAI")
    assert provider is not None
    for capability in ("text_generation", "embedding", "image_generation", "tts", "stt"):
        names = {field["name"]: field for field in provider["fields"][capability]}
        assert names["endpoint"]["required"] is False
        assert names["endpoint"]["displayName"] == "Base URL (optional)"
        assert names["organizationId"]["required"] is False


def test_openai_compatible_exposes_image_tts_and_stt():
    provider = ai_model_registry.get_provider("openAICompatible")
    assert set(provider["capabilities"]) >= {
        "text_generation",
        "embedding",
        "image_generation",
        "tts",
        "stt",
    }
    for capability in ("image_generation", "tts", "stt"):
        names = {field["name"]: field for field in provider["fields"][capability]}
        assert names["endpoint"]["required"] is True
        assert names["apiKey"]["required"] is False


def test_fireworks_exposes_embedding_and_together_endpoint_is_required():
    fireworks = ai_model_registry.get_provider("fireworks")
    assert "embedding" in fireworks["capabilities"]
    embedding_fields = {field["name"]: field for field in fireworks["fields"]["embedding"]}
    assert embedding_fields["endpoint"]["required"] is True
    together = ai_model_registry.get_provider("together")
    for capability in ("text_generation", "embedding"):
        endpoint = next(field for field in together["fields"][capability] if field["name"] == "endpoint")
        assert endpoint["required"] is True
