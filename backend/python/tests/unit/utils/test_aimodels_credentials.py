"""A model client only uses an API key saved for that model, never one from the environment."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.config.ai_models.providers  # noqa: F401  (registers the providers)
from app.config.ai_models.registry import ai_model_registry
from app.utils.aimodels import (
    KEYLESS_PROVIDERS,
    NON_KEY_PROVIDERS,
    EmbeddingProvider,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
    openai_async_client_kwargs,
    require_explicit_api_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

BLANKS = [None, "", "   "]


@pytest.fixture(autouse=True)
def server_env_keys(monkeypatch) -> None:
    for name in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.setenv(name, "env-key")


@pytest.fixture
def server_credentials_refused(monkeypatch) -> None:
    monkeypatch.setattr("app.utils.aimodels._ambient_cloud_credentials_allowed", lambda config: False)


@pytest.mark.parametrize("blank", BLANKS)
def test_api_key_provider_with_blank_key_is_refused(blank) -> None:
    with pytest.raises(ValueError, match="Save an API key"):
        require_explicit_api_key("gemini", {"apiKey": blank})


def test_missing_key_is_refused_and_saved_key_kept_on_a_copy() -> None:
    with pytest.raises(ValueError):
        require_explicit_api_key("openAI", {})
    original = {"apiKey": "sk-saved", "model": "m"}
    result = require_explicit_api_key("openAI", original)
    assert result == original and result is not original


@pytest.mark.parametrize("provider", sorted(KEYLESS_PROVIDERS))
@pytest.mark.parametrize("blank", BLANKS)
def test_keyless_provider_gets_placeholder_never_none(provider, blank) -> None:
    assert require_explicit_api_key(provider, {"apiKey": blank})["apiKey"] == "not-needed"
    assert require_explicit_api_key(provider, {})["apiKey"] == "not-needed"


def test_keyless_provider_keeps_a_saved_key() -> None:
    assert require_explicit_api_key("ollama", {"apiKey": "k"})["apiKey"] == "k"


@pytest.mark.parametrize("provider", sorted(NON_KEY_PROVIDERS))
def test_non_key_provider_passes_through_unchanged(provider) -> None:
    assert require_explicit_api_key(provider, {"region": "x"}) == {"region": "x"}


def test_keyless_sets_match_provider_registry() -> None:
    optional: set[str] = set()
    no_key_field: set[str] = set()
    for meta in ai_model_registry.list_providers():
        key_fields = [
            f for fields in meta["fields"].values() for f in fields if f["name"] == "apiKey"
        ]
        if any(not f["required"] for f in key_fields):
            optional.add(meta["providerId"])
        elif not key_fields:
            no_key_field.add(meta["providerId"])
    assert optional <= KEYLESS_PROVIDERS, optional - KEYLESS_PROVIDERS
    assert no_key_field <= NON_KEY_PROVIDERS, no_key_field - NON_KEY_PROVIDERS
    # litellmProxy is the one gateway that is keyless in practice but registers a required key.
    assert KEYLESS_PROVIDERS - optional == {"litellmProxy"}
    registered = {m["providerId"] for m in ai_model_registry.list_providers()}
    assert (KEYLESS_PROVIDERS | NON_KEY_PROVIDERS) <= registered
    assert not KEYLESS_PROVIDERS & NON_KEY_PROVIDERS


def test_openai_async_client_kwargs_only_passes_a_saved_key() -> None:
    assert "api_key" not in openai_async_client_kwargs({})
    assert "api_key" not in openai_async_client_kwargs({"apiKey": ""})
    assert openai_async_client_kwargs({"apiKey": "sk"})["api_key"] == "sk"


@pytest.mark.asyncio
async def test_probe_never_sends_environment_key(server_credentials_refused) -> None:
    from app.api.routes.health import _probe_openai_listed_model

    created: dict = {}
    client = MagicMock()
    client.models.retrieve = MagicMock(side_effect=lambda m: _done(MagicMock(id=m)))
    client.close = MagicMock(side_effect=lambda: _done(None))

    def factory(**kwargs: object) -> MagicMock:
        created.update(kwargs)
        return client

    with patch("openai.AsyncOpenAI", side_effect=factory):
        await _probe_openai_listed_model({}, "m")
    assert created["api_key"] == "not-needed"


async def _done(value: object) -> object:
    return value


def _config(**configuration: object) -> dict:
    return {"configuration": {"model": "m", "endpoint": "http://localhost:9/v1", **configuration}}


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("provider", ["gemini", "openAI", "anthropic", "azureAI", "openRouter"])
def test_generator_refuses_blank_key(server_credentials_refused, provider, blank) -> None:
    with pytest.raises(ValueError, match="Save an API key"):
        get_generator_model(provider, _config(apiKey=blank, endpoint=""))


@pytest.mark.parametrize("provider", ["gemini", "openAI", "azureAI", "openRouter"])
def test_embedding_refuses_blank_key(server_credentials_refused, provider) -> None:
    with pytest.raises(ValueError, match="Save an API key"):
        get_embedding_model(provider, _config(apiKey="", endpoint=""))


@pytest.mark.parametrize("fn", [get_image_generation_model, get_tts_model, get_stt_model])
@pytest.mark.parametrize("provider", ["openAI", "gemini", "openRouter"])
def test_media_builders_refuse_blank_key(server_credentials_refused, fn, provider) -> None:
    with pytest.raises(ValueError, match="Save an API key"):
        fn(provider, _config(apiKey=None, endpoint=""))


@pytest.mark.parametrize("provider", ["openRouter", "minimax"])
def test_fixed_host_provider_refuses_blank_key_even_when_server_key_allowed(provider: str) -> None:
    with pytest.raises(ValueError, match="Save an API key"):
        get_generator_model(provider, _config(apiKey="", endpoint=""))


@pytest.mark.parametrize("fn", [get_image_generation_model, get_tts_model, get_stt_model])
def test_openrouter_media_refuses_blank_key_even_when_server_key_allowed(fn: object) -> None:
    with pytest.raises(ValueError, match="Save an API key"):
        fn("openRouter", _config(apiKey=None, endpoint=""))


@pytest.mark.parametrize("fn", [get_image_generation_model, get_tts_model, get_stt_model])
def test_media_litellm_adapter_never_gets_none(server_credentials_refused, fn) -> None:
    adapter = fn("litellmProxy", _config(apiKey=None))
    assert adapter._api_key == "not-needed"


def test_litellm_generator_never_gets_blank_key(server_credentials_refused) -> None:
    with patch("langchain_openai.ChatOpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        get_generator_model("litellmProxy", _config(apiKey=None))
    assert mock_cls.call_args.kwargs["api_key"] == "not-needed"


def test_litellm_embedding_never_gets_blank_key(server_credentials_refused) -> None:
    with patch("langchain_openai.embeddings.OpenAIEmbeddings") as mock_cls:
        mock_cls.return_value = MagicMock()
        get_embedding_model(EmbeddingProvider.LITELLM_PROXY.value, _config(apiKey=""))
    assert mock_cls.call_args.kwargs["api_key"] == "not-needed"


def test_azure_ai_receives_the_saved_key() -> None:
    with patch("langchain_openai.ChatOpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        get_generator_model("azureAI", _config(apiKey="azure-saved"))
    assert mock_cls.call_args.kwargs["api_key"] == "azure-saved"


def test_google_chat_never_receives_blank_key(server_credentials_refused) -> None:
    with patch("langchain_google_genai.ChatGoogleGenerativeAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        get_generator_model("gemini", _config(apiKey="g-saved"))
        assert mock_cls.call_args.kwargs["google_api_key"] == "g-saved"
        with pytest.raises(ValueError):
            get_generator_model("gemini", _config(apiKey=""))


@pytest.mark.parametrize(
    "module,cls",
    [("gemini_provider", "GeminiMultimodalProvider"), ("cohere_provider", "CohereMultimodalProvider")],
)
@pytest.mark.parametrize("blank", BLANKS)
def test_multimodal_providers_refuse_blank_key(module, cls, blank) -> None:
    import importlib

    provider = getattr(importlib.import_module(f"app.services.embeddings.multimodal.{module}"), cls)
    with pytest.raises(ValueError, match="Save an API key"):
        provider(api_key=blank, model_name="m")
    assert provider(api_key="saved", model_name="m").api_key == "saved"


def _auth_headers(*clients: object) -> list:
    return [c._client.headers.get("authorization") for c in clients]


@pytest.mark.parametrize("saved,expected", [(None, "Bearer not-needed"), ("saved-key", "Bearer saved-key")])
def test_ollama_clients_never_send_environment_key(server_credentials_refused, saved, expected) -> None:
    llm = get_generator_model("ollama", _config(apiKey=saved))
    emb = get_embedding_model("ollama", _config(apiKey=saved))
    headers = _auth_headers(llm._client, llm._async_client, emb._client, emb._async_client)
    assert headers == [expected] * 4


# --- When the server's own credentials are allowed ------------------------

def _allow_recorder(monkeypatch: pytest.MonkeyPatch) -> list:
    seen = []
    monkeypatch.setattr(
        "app.utils.aimodels._ambient_cloud_credentials_allowed",
        lambda config: seen.append(config) or True,
    )
    return seen


def test_allowed_blank_key_is_passed_through_unchanged(monkeypatch) -> None:
    seen = _allow_recorder(monkeypatch)
    entry = {"configuration": {"apiKey": None}}
    assert require_explicit_api_key("gemini", {"apiKey": None}, entry) == {"apiKey": None}
    assert require_explicit_api_key("ollama", {}, entry) == {}
    assert seen == [entry, entry]


def test_refused_entry_raises_and_fills_placeholders(server_credentials_refused) -> None:
    entry = {"configuration": {}}
    with pytest.raises(ValueError, match="Save an API key"):
        require_explicit_api_key("openAI", {}, entry)
    assert require_explicit_api_key("lmStudio", {}, entry)["apiKey"] == "not-needed"


def test_decision_is_only_asked_for_a_blank_key_on_a_key_provider(monkeypatch) -> None:
    seen = _allow_recorder(monkeypatch)
    require_explicit_api_key("openAI", {"apiKey": "saved"}, {"configuration": {}})
    require_explicit_api_key("bedrock", {}, {"configuration": {}})
    assert seen == []


def test_allowed_builders_use_the_environment_fallback_as_before() -> None:
    with patch("langchain_openai.ChatOpenAI") as chat:
        chat.return_value = MagicMock()
        get_generator_model("openAI", _config(apiKey=None, endpoint=""))
    assert chat.call_args.kwargs["api_key"] is None


def test_allowed_generator_builders_keep_their_own_placeholders() -> None:
    with patch("langchain_openai.ChatOpenAI") as chat:
        chat.return_value = MagicMock()
        get_generator_model("lmStudio", _config(apiKey=None))
    assert chat.call_args.kwargs["api_key"] == "lm-studio"


def test_allowed_ollama_clients_keep_the_sdk_defaults_on_the_servers_own_endpoint() -> None:
    llm = get_generator_model("ollama", _config(apiKey=None, endpoint=""))
    emb = get_embedding_model("ollama", _config(apiKey=None, endpoint=""))
    assert _auth_headers(llm._client, llm._async_client, emb._client, emb._async_client) == ["Bearer env-key"] * 4


@pytest.mark.parametrize("saved,expected", [(None, "Bearer not-needed"), ("saved-key", "Bearer saved-key")])
def test_allowed_ollama_clients_send_no_server_key_to_a_typed_endpoint(saved, expected) -> None:
    llm = get_generator_model("ollama", _config(apiKey=saved))
    emb = get_embedding_model("ollama", _config(apiKey=saved))
    assert _auth_headers(llm._client, llm._async_client, emb._client, emb._async_client) == [expected] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_key,allowed,saved,expected",
    [
        ("env-key", True, None, None),
        (None, True, None, "not-needed"),
        ("env-key", False, None, "not-needed"),
        ("env-key", False, "saved", "saved"),
        ("env-key", True, "saved", "saved"),
    ],
)
async def test_probe_api_key(monkeypatch, env_key, allowed, saved, expected) -> None:
    from app.api.routes.health import _probe_openai_listed_model

    if env_key is None:
        monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr("app.utils.aimodels._ambient_cloud_credentials_allowed", lambda config: allowed)
    created: dict = {}
    client = MagicMock()
    client.models.retrieve = MagicMock(side_effect=lambda m: _done(MagicMock(id=m)))
    client.close = MagicMock(side_effect=lambda: _done(None))

    def factory(**kwargs: object) -> MagicMock:
        created.update(kwargs)
        return client

    with patch("openai.AsyncOpenAI", side_effect=factory):
        await _probe_openai_listed_model({"apiKey": saved}, "m")
    assert created.get("api_key") == expected


@pytest.mark.asyncio
async def test_probe_decides_on_the_real_provider(monkeypatch) -> None:
    from app.api.routes.health import _probe_openai_listed_model

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    seen: list = []
    monkeypatch.setattr(
        "app.utils.aimodels._ambient_cloud_credentials_allowed", lambda config: seen.append(config) or True,
    )
    client = MagicMock()
    client.models.retrieve = MagicMock(side_effect=lambda m: _done(MagicMock(id=m)))
    client.close = MagicMock(side_effect=lambda: _done(None))
    with patch("openai.AsyncOpenAI", return_value=client):
        await _probe_openai_listed_model({}, "m", "openAI")
    assert [config["provider"] for config in seen] == ["openAI"]


@pytest.mark.parametrize(
    "module,cls",
    [("gemini_provider", "GeminiMultimodalProvider"), ("cohere_provider", "CohereMultimodalProvider")],
)
def test_multimodal_providers_pass_a_blank_key_when_allowed(module, cls) -> None:
    import importlib

    provider = getattr(importlib.import_module(f"app.services.embeddings.multimodal.{module}"), cls)
    assert provider(api_key=None, model_name="m", allow_ambient=True).api_key is None


@pytest.mark.parametrize("name", ["cohere", "gemini"])
def test_multimodal_factory_passes_the_decision(name) -> None:
    from app.services.embeddings.multimodal.config import MultimodalProviderConfig
    from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory

    built = MultimodalEmbeddingFactory.create(
        MultimodalProviderConfig(provider=name, model_name="m", allow_ambient_credentials=True)
    )
    assert built.api_key is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cohere", "gemini"])
async def test_health_image_probe_asks_for_a_decision_on_key_providers(monkeypatch, provider) -> None:
    from app.api.routes.health import (
        _ImageEmbeddingSettingsError,
        _probe_image_embedding,
    )

    seen = _allow_recorder(monkeypatch)
    with patch(
        "app.services.embeddings.multimodal.factory.MultimodalEmbeddingFactory.create",
        side_effect=RuntimeError("stop"),
    ) as create, pytest.raises(_ImageEmbeddingSettingsError):
        await _probe_image_embedding({"provider": provider, "configuration": {}}, "m", 8, MagicMock())
    assert create.call_args.args[0].allow_ambient_credentials is True
    assert seen == [{"provider": provider, "configuration": {}}]


@pytest.mark.parametrize(
    "path,builder",
    [
        ("perform_image_generation_health_check", "get_image_generation_model"),
        ("perform_tts_health_check", "get_tts_model"),
        ("perform_stt_health_check", "get_stt_model"),
    ],
)
@pytest.mark.asyncio
async def test_media_health_checks_drop_annotations_from_the_body(path, builder) -> None:
    import app.api.routes.health as health

    body = {"provider": "openAI", "_note": True, "configuration": {"model": "m", "apiKey": "k"}}
    with patch.object(health, builder, side_effect=RuntimeError("stop")) as build:
        await getattr(health, path)(body, MagicMock())
    assert "_note" not in build.call_args.kwargs["config"]
    assert body["_note"] is True


@pytest.mark.asyncio
async def test_llm_health_check_drops_annotations_from_the_body() -> None:
    import app.api.routes.health as health

    body = {"provider": "openAI", "_note": True, "configuration": {"model": "m", "apiKey": "k"}}
    with patch.object(health, "get_generator_model", side_effect=RuntimeError("stop")) as build:
        with pytest.raises(RuntimeError):
            await health._check_one_llm(body, "m", MagicMock())
    assert "_note" not in build.call_args.kwargs["config"]
@pytest.mark.parametrize("is_default", [True, False])
@pytest.mark.asyncio
async def test_bulk_embedding_check_drops_annotations_from_the_body(is_default) -> None:
    import app.api.routes.health as health

    body = {"provider": "openAI", "isDefault": is_default, "_note": True, "configuration": {"model": "m", "apiKey": "k"}}
    request = MagicMock()
    request.app.container.retrieval_service = AsyncMock(return_value=MagicMock())
    with patch.object(health, "get_embedding_model", return_value=MagicMock()) as build:
        await health.initialize_embedding_model(request, [body])
    assert "_note" not in build.call_args.args[1]
    assert body["_note"] is True


@pytest.mark.asyncio
async def test_decision_health_check_drops_annotations_from_the_body() -> None:
    import app.api.routes.health as health

    body = {"provider": "openAI", "_note": True, "configuration": {"model": "m", "apiKey": "k"}}
    with patch.object(health, "get_decision_model", side_effect=RuntimeError("stop")) as build:
        await health.perform_decision_health_check(body, MagicMock())
    assert "_note" not in build.call_args.kwargs["config"]


# --- Bedrock: the server's own AWS identity -------------------------------

_KEYS_REQUIRED = "Enter an AWS access key and secret for this model."


def _bedrock(configuration: dict, *, allow_ambient: bool) -> MagicMock:
    from app.utils.aimodels import _create_bedrock_client

    with patch("boto3.Session") as session:
        try:
            _create_bedrock_client(configuration, allow_ambient=allow_ambient)
        finally:
            made = session
    return made


def test_bedrock_both_keys_are_used() -> None:
    made = _bedrock(
        {"region": "us-east-1", "awsAccessKeyId": " AK ", "awsAccessSecretKey": " SK "},
        allow_ambient=False,
    )
    made.assert_called_once_with(
        aws_access_key_id="AK", aws_secret_access_key="SK", region_name="us-east-1",
    )


@pytest.mark.parametrize("present", ["awsAccessKeyId", "awsAccessSecretKey"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_bedrock_one_key_only_is_refused_when_server_identity_is_refused(present, blank) -> None:
    other = "awsAccessSecretKey" if present == "awsAccessKeyId" else "awsAccessKeyId"
    with patch("boto3.Session") as session, pytest.raises(ValueError, match=_KEYS_REQUIRED):
        from app.utils.aimodels import _create_bedrock_client

        _create_bedrock_client({"region": "r", present: "x", other: blank}, allow_ambient=False)
    session.assert_not_called()


@pytest.mark.parametrize("present", ["awsAccessKeyId", "awsAccessSecretKey"])
def test_bedrock_one_key_only_uses_the_server_identity_with_a_warning(present, caplog) -> None:
    import logging

    other = "awsAccessSecretKey" if present == "awsAccessKeyId" else "awsAccessKeyId"
    config = {"region": "r", present: "only-one-value", other: ""}
    with caplog.at_level(logging.WARNING):
        made = _bedrock(config, allow_ambient=True)
    assert made.call_args.kwargs == {"region_name": "r"}
    assert "Only one of" in caplog.text
    assert "only-one-value" not in caplog.text


def test_bedrock_no_keys_ambient_allowed_uses_default_chain() -> None:
    assert _bedrock({"region": "eu-west-1"}, allow_ambient=True).call_args.kwargs == {
        "region_name": "eu-west-1"
    }


def test_bedrock_no_keys_ambient_refused_raises() -> None:
    with patch("boto3.Session") as session, pytest.raises(ValueError, match=_KEYS_REQUIRED):
        from app.utils.aimodels import _create_bedrock_client

        _create_bedrock_client({"region": "r"}, allow_ambient=False)
    session.assert_not_called()


def test_bedrock_allow_ambient_has_no_default() -> None:
    from app.utils.aimodels import _create_bedrock_client

    with pytest.raises(TypeError):
        _create_bedrock_client({"region": "r"})


def test_ambient_credentials_allowed_by_default() -> None:
    from app.edition_services import ambient_cloud_credentials_allowed

    assert ambient_cloud_credentials_allowed({"configuration": {}}) is True


def test_bedrock_chat_builder_asks_the_edition(monkeypatch) -> None:
    from app.utils.aimodels import _get_generator_model_impl

    seen = {}
    monkeypatch.setattr(
        "app.utils.aimodels._ambient_cloud_credentials_allowed",
        lambda config: seen.setdefault("config", config) and False,
    )
    entry = {"provider": "bedrock", "configuration": {"model": "m", "region": "r"}}
    with patch("boto3.Session") as session, pytest.raises(ValueError, match=_KEYS_REQUIRED):
        _get_generator_model_impl("bedrock", entry)
    assert seen["config"] is entry
    session.assert_not_called()


def test_bedrock_embedding_builder_asks_the_edition(monkeypatch) -> None:
    monkeypatch.setattr("app.utils.aimodels._ambient_cloud_credentials_allowed", lambda config: False)
    entry = {"provider": "bedrock", "configuration": {"model": "m", "region": "r"}}
    with patch("boto3.Session") as session, pytest.raises(ValueError, match=_KEYS_REQUIRED):
        get_embedding_model("bedrock", entry)
    session.assert_not_called()


def test_bedrock_discovery_listing_is_unstamped(monkeypatch) -> None:
    from app.services.ai_models.discovery.strategies import list_bedrock_pages

    seen = []
    monkeypatch.setattr(
        "app.utils.aimodels._ambient_cloud_credentials_allowed",
        lambda config: seen.append(config) or False,
    )
    with patch("boto3.Session") as session, pytest.raises(ValueError, match=_KEYS_REQUIRED):
        list_bedrock_pages({"region": "r"})
    assert seen == [{"provider": "bedrock", "configuration": {"region": "r"}}]
    session.assert_not_called()


@pytest.mark.asyncio
async def test_bedrock_multimodal_provider_rules() -> None:
    from app.exceptions.indexing_exceptions import EmbeddingError
    from app.services.embeddings.multimodal.bedrock_provider import (
        BedrockMultimodalProvider,
    )

    def build(**kw: object) -> BedrockMultimodalProvider:
        return BedrockMultimodalProvider(model_name="m", region_name="r", **kw)

    with patch("boto3.client") as client:
        for kw in (
            {},
            {"aws_access_key_id": "AK"},
            {"aws_secret_access_key": " "},
        ):
            with pytest.raises(EmbeddingError, match=_KEYS_REQUIRED):
                await build(**kw).embed_images(["x"])
        client.assert_not_called()

        client.return_value.invoke_model.side_effect = RuntimeError("stop")
        await build(allow_ambient=True).embed_images(["x"])
        assert client.call_args.kwargs == {"service_name": "bedrock-runtime"}
        await build(aws_access_key_id="AK", allow_ambient=True).embed_images(["x"])
        assert client.call_args.kwargs == {"service_name": "bedrock-runtime"}
        await build(aws_access_key_id="AK", aws_secret_access_key="SK").embed_images(["x"])
        assert client.call_args.kwargs["aws_access_key_id"] == "AK"


@pytest.mark.asyncio
async def test_health_probe_reads_the_real_secret_field_name() -> None:
    from app.api.routes.health import _probe_image_embedding

    captured = {}

    class Boom(Exception):
        pass

    def create(config) -> Never:
        captured["config"] = config
        raise Boom

    from app.api.routes.health import _ImageEmbeddingSettingsError

    with patch(
        "app.services.embeddings.multimodal.factory.MultimodalEmbeddingFactory.create", create
    ), pytest.raises(_ImageEmbeddingSettingsError):
        await _probe_image_embedding(
            {"provider": "bedrock", "configuration": {"awsAccessKeyId": "AK", "awsAccessSecretKey": "SK"}},
            "m", 1024, MagicMock(),
        )
    assert captured["config"].aws_secret_access_key == "SK"


@pytest.mark.asyncio
async def test_missing_credentials_reach_the_admin_as_a_clear_400() -> None:
    import json

    from app.api.routes.health import _model_setup_failed_response
    from app.utils.aimodels import CredentialsRequiredError

    response = await _model_setup_failed_response(
        "chat model", {"provider": "bedrock"}, "m", CredentialsRequiredError(_KEYS_REQUIRED),
    )
    assert response.status_code == 400
    assert json.loads(response.body)["message"] == _KEYS_REQUIRED


def test_bedrock_chat_control_plane_client_is_ours() -> None:
    from app.utils.aimodels import _get_generator_model_impl

    entry = {
        "provider": "bedrock",
        "configuration": {
            "model": "anthropic.claude-3-haiku-20240307-v1:0", "region": "us-east-1",
            "awsAccessKeyId": "AK", "awsAccessSecretKey": "SK",
        },
    }
    with patch("boto3.Session") as session, patch("langchain_aws.ChatBedrockConverse") as chat:
        _get_generator_model_impl("bedrock", entry)
    assert [c.kwargs["aws_access_key_id"] for c in session.call_args_list] == ["AK", "AK"]
    kwargs = chat.call_args.kwargs
    assert kwargs["client"] is session.return_value.client.return_value
    assert kwargs["bedrock_client"] is session.return_value.client.return_value
    assert [c.args[0] for c in session.return_value.client.call_args_list] == ["bedrock-runtime", "bedrock"]


def test_bedrock_chat_without_keys_or_permission_builds_no_client(monkeypatch) -> None:
    from app.utils.aimodels import _get_generator_model_impl

    monkeypatch.setattr("app.utils.aimodels._ambient_cloud_credentials_allowed", lambda config: False)
    entry = {"provider": "bedrock", "configuration": {"model": "m", "region": "r"}}
    with patch("boto3.Session") as session, patch("langchain_aws.ChatBedrockConverse") as chat, \
            pytest.raises(ValueError, match=_KEYS_REQUIRED):
        _get_generator_model_impl("bedrock", entry)
    session.assert_not_called()
    chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_health_blank_key_is_a_clear_400(blank) -> None:
    import json

    from app.api.routes.health import _model_setup_failed_response

    try:
        require_explicit_api_key("openai", {"apiKey": blank})
    except ValueError as exc:  # still a ValueError for existing callers
        error = exc
    response = await _model_setup_failed_response("chat model", {"provider": "openai"}, "m", error)
    assert response.status_code == 400
    assert json.loads(response.body)["message"] == "Save an API key for this model."


# --- A blank key never takes the server's key to another host, in any mode ---

ATTACKER = "https://attacker.example/v1"


@pytest.mark.parametrize("provider", ["openAI", "openAICompatible", "litellmProxy", "ollama"])
@pytest.mark.parametrize("blank", [None, "", "  "])
def test_allowed_blank_key_off_the_providers_host_gets_the_placeholder(monkeypatch, provider, blank) -> None:
    _allow_recorder(monkeypatch)
    entry = {"configuration": {}}
    out = require_explicit_api_key(provider, {"apiKey": blank, "endpoint": ATTACKER}, entry)
    assert out["apiKey"] == "not-needed"


@pytest.mark.parametrize("provider", ["azureOpenAI", "azureAI", "together", "fireworks"])
def test_allowed_blank_key_off_the_providers_host_is_refused(monkeypatch, provider) -> None:
    _allow_recorder(monkeypatch)
    with pytest.raises(ValueError, match="Save an API key"):
        require_explicit_api_key(provider, {"apiKey": None, "endpoint": ATTACKER}, {"configuration": {}})


@pytest.mark.parametrize("provider,endpoint", [
    ("openAI", "https://api.openai.com/v1"),
    ("openAI", ""),
    ("litellmProxy", "https://api.openai.com/v1"),
    ("fireworks", "https://api.fireworks.ai/inference/v1"),
    ("together", "https://api.together.xyz/v1"),
    ("azureOpenAI", ""),
    ("ollama", "https://ollama.com"),
])
def test_allowed_blank_key_on_the_providers_own_host_keeps_the_fallback(monkeypatch, provider, endpoint) -> None:
    _allow_recorder(monkeypatch)
    configuration = {"apiKey": None, "endpoint": endpoint}
    assert require_explicit_api_key(provider, configuration, {"configuration": configuration}) == configuration


@pytest.mark.parametrize("endpoint", [
    "http://api.openai.com/v1",  # plain http: the key would cross the network in the clear
    "https://api.openai.com.attacker.example/v1",
    "https://api.openai.com@attacker.example/v1",
    "https://attacker.example/v1?h=api.openai.com",
    "http://[::1",
])
def test_openai_host_check_needs_https_on_openais_own_host(monkeypatch, endpoint) -> None:
    _allow_recorder(monkeypatch)
    out = require_explicit_api_key("openAI", {"apiKey": None, "endpoint": endpoint}, {"configuration": {}})
    assert out["apiKey"] == "not-needed"


# --- Provider openAI with no key, pointed at another host -------------------

_PROXY = "https://proxy.example.com/v1"
MEDIA = [get_image_generation_model, get_tts_model, get_stt_model]


@pytest.mark.parametrize("fn", MEDIA)
@pytest.mark.parametrize("blank", [None, ""])
def test_official_openai_media_builders_send_the_placeholder_off_openai(fn, blank) -> None:
    # Allowed (the default): the client would read OPENAI_API_KEY and send it to the proxy.
    assert fn("openAI", _config(apiKey=blank, endpoint=_PROXY))._api_key == "not-needed"


@pytest.mark.parametrize("fn", MEDIA)
def test_official_openai_media_builders_keep_today_on_openai(fn) -> None:
    assert fn("openAI", _config(apiKey=None, endpoint="https://api.openai.com/v1"))._api_key is None
    assert fn("openAI", _config(apiKey=None, endpoint=""))._api_key is None
    assert fn("openAI", _config(apiKey="saved", endpoint=_PROXY))._api_key == "saved"


# OpenAI's own regional hosts and the trailing-dot form; lookalikes are another host.
OPENAI_OWN_HOSTS = [
    "https://us.api.openai.com/v1", "https://eu.api.openai.com/v1",
    "https://api.openai.com./v1", "https://EU.API.OPENAI.COM./v1",
]
LOOKALIKE_HOSTS = ["https://api.openai.com.evil.example/v1", "https://not-api.openai.com/v1"]


@pytest.mark.parametrize("fn", MEDIA)
@pytest.mark.parametrize("endpoint", OPENAI_OWN_HOSTS)
def test_official_openai_media_builders_keep_today_on_regional_hosts(fn, endpoint, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-canary")
    assert fn("openAI", _config(apiKey=None, endpoint=endpoint))._api_key is None


@pytest.mark.parametrize("fn", MEDIA)
@pytest.mark.parametrize("endpoint", LOOKALIKE_HOSTS)
def test_official_openai_media_builders_treat_lookalikes_as_another_host(fn, endpoint, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-canary")
    assert fn("openAI", _config(apiKey=None, endpoint=endpoint))._api_key == "not-needed"


def test_refused_openai_off_openai_gets_the_placeholder(server_credentials_refused) -> None:
    entry = {"configuration": {}}
    out = require_explicit_api_key("openAI", {"apiKey": None, "endpoint": _PROXY}, entry)
    assert out["apiKey"] == "not-needed"
    out = require_explicit_api_key("openAI", {"apiKey": None, "endpoint": "http://[::1"}, entry)
    assert out["apiKey"] == "not-needed"
    with pytest.raises(ValueError, match="Save an API key"):
        require_explicit_api_key("openAI", {"apiKey": None, "endpoint": "https://api.openai.com/v1"}, entry)


def test_refused_openai_chat_and_embedding_off_openai_never_get_none(server_credentials_refused, monkeypatch) -> None:
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "false")
    with patch("langchain_openai.ChatOpenAI") as chat:
        chat.return_value = MagicMock()
        get_generator_model("openAI", _config(apiKey=None, endpoint=_PROXY))
    assert chat.call_args.kwargs["api_key"] == "not-needed"
    with patch("langchain_openai.embeddings.OpenAIEmbeddings") as emb:
        emb.return_value = MagicMock()
        get_embedding_model("openAI", _config(apiKey=None, endpoint=_PROXY))
    assert emb.call_args.kwargs["api_key"] == "not-needed"


# --- A health check against an attacker's endpoint, in the default policy ----

_CANARY = "sk-server-canary"


@pytest.fixture
def attacker_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[dict[str, str]]]]:
    """A local HTTP server standing in for an attacker's host; records every request's headers."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            seen.append({k.lower(): v for k, v in self.headers.items()})
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = json.dumps({"error": {"message": "no", "type": "invalid_request_error"}}).encode()
            self.send_response(401)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TOGETHER_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.setenv(name, _CANARY)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://own-resource.openai.azure.com")
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "false")
    monkeypatch.delenv("MODEL_CREDENTIAL_POLICY", raising=False)  # the default: log, server credentials allowed
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", seen
    finally:
        server.shutdown()


_HEALTH_CASES = [
    ("openAI", "gpt-4o-mini"),
    ("azureOpenAI", "gpt-4o-mini"),
    ("azureAI", "gpt-4o-mini"),
    ("azureAI", "claude-sonnet-4"),
    ("openAICompatible", "m"),
    ("litellmProxy", "m"),
    ("together", "m"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", _HEALTH_CASES)
async def test_llm_health_check_never_sends_the_server_key_to_an_attacker(attacker_endpoint, provider, model) -> None:
    import logging

    from app.api.routes.health import perform_llm_health_check

    endpoint, seen = attacker_endpoint
    body = {"provider": provider, "configuration": {
        "model": model, "endpoint": endpoint, "apiKey": None, "deploymentName": model,
    }}
    await perform_llm_health_check(body, logging.getLogger("test"))
    assert all(_CANARY not in value for headers in seen for value in headers.values())
    if provider in ("openAI", "openAICompatible", "litellmProxy"):
        assert seen, "the probe should still reach a keyless endpoint, with the placeholder"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openAI", "azureOpenAI", "azureAI", "openAICompatible", "litellmProxy", "together"])
async def test_embedding_health_check_never_sends_the_server_key_to_an_attacker(attacker_endpoint, provider) -> None:
    import logging

    from app.api.routes.health import perform_embedding_health_check

    endpoint, seen = attacker_endpoint
    body = {"provider": provider, "configuration": {
        "model": "text-embedding-3-small", "endpoint": endpoint, "apiKey": None,
    }}
    await perform_embedding_health_check(MagicMock(), body, logging.getLogger("test"))
    assert all(_CANARY not in value for headers in seen for value in headers.values())
    if provider in ("openAI", "openAICompatible", "litellmProxy"):
        assert seen, "the probe should still reach a keyless endpoint, with the placeholder"
