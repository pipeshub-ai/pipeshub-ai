"""Tests for app.config.ai_models.registry — decorator, builder, and registry class."""

import pytest

from app.config.ai_models.registry import (
    AIModelProvider,
    AIModelProviderBuilder,
    AIModelRegistry,
    _default_provider_id,
)
from app.config.ai_models.types import AIModelField, ModelCapability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider_class(
    name="TestProvider",
    provider_id="testProvider",
    description="A test provider",
    capabilities=None,
    icon_path="/assets/icons/test.svg",
    color="#000000",
    is_popular=False,
    fields=None,
):
    caps = capabilities or [ModelCapability.TEXT_GENERATION]

    @AIModelProvider(
        name=name,
        provider_id=provider_id,
        description=description,
        capabilities=caps,
        icon_path=icon_path,
        color=color,
        is_popular=is_popular,
        fields=fields,
    )
    class FakeProvider:
        pass

    return FakeProvider


# ---------------------------------------------------------------------------
# TestAIModelProviderDecorator
# ---------------------------------------------------------------------------

class TestAIModelProviderDecorator:

    def test_sets_metadata_on_class(self):
        cls = _make_provider_class()
        assert hasattr(cls, "_provider_metadata")
        assert cls._provider_metadata["name"] == "TestProvider"
        assert cls._provider_metadata["providerId"] == "testProvider"

    def test_validates_name_required(self):
        with pytest.raises(ValueError, match="name"):
            AIModelProvider(
                name="",
                provider_id="x",
                capabilities=[ModelCapability.TEXT_GENERATION],
            )

    def test_validates_provider_id_required(self):
        with pytest.raises(ValueError, match="id"):
            AIModelProvider(
                name="X",
                provider_id="",
                capabilities=[ModelCapability.TEXT_GENERATION],
            )

    def test_validates_capabilities_required(self):
        with pytest.raises(ValueError, match="capability"):
            AIModelProvider(
                name="X",
                provider_id="x",
                capabilities=[],
            )

    def test_normalizes_capability_enums_to_strings(self):
        cls = _make_provider_class(capabilities=[ModelCapability.EMBEDDING])
        caps = cls._provider_metadata["capabilities"]
        assert caps == ["embedding"]
        assert all(isinstance(c, str) for c in caps)

    def test_stores_defaults(self):
        cls = _make_provider_class()
        meta = cls._provider_metadata
        assert meta["description"] == "A test provider"
        assert meta["isPopular"] is False
        assert isinstance(meta["fields"], dict)

    def test_fields_serialized(self):
        fields = {
            "text_generation": [AIModelField(name="apiKey", display_name="API Key")]
        }
        cls = _make_provider_class(fields=fields)
        meta = cls._provider_metadata
        assert "text_generation" in meta["fields"]
        assert meta["fields"]["text_generation"][0]["name"] == "apiKey"


# ---------------------------------------------------------------------------
# TestAIModelProviderBuilder
# ---------------------------------------------------------------------------

class TestAIModelProviderBuilder:

    def test_basic_builder(self):
        decorator = (
            AIModelProviderBuilder("MyProvider", "myProvider")
            .with_description("desc")
            .with_capabilities([ModelCapability.TEXT_GENERATION])
            .build_decorator()
        )

        @decorator
        class P:
            pass

        assert P._provider_metadata["name"] == "MyProvider"
        assert P._provider_metadata["description"] == "desc"
        assert "text_generation" in P._provider_metadata["capabilities"]

    def test_shared_fields_replicated_per_capability(self):
        shared_field = AIModelField(name="apiKey", display_name="API Key")
        builder = (
            AIModelProviderBuilder("Multi", "multi")
            .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING])
            .add_field(shared_field)
        )
        dec = builder.build_decorator()

        @dec
        class P:
            pass

        fields = P._provider_metadata["fields"]
        assert "apiKey" in [f["name"] for f in fields["text_generation"]]
        assert "apiKey" in [f["name"] for f in fields["embedding"]]

    def test_per_capability_field(self):
        cap_field = AIModelField(name="contextLength", display_name="Context Length")
        builder = (
            AIModelProviderBuilder("Cap", "cap")
            .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING])
            .add_field(cap_field, capability=ModelCapability.TEXT_GENERATION)
        )
        dec = builder.build_decorator()

        @dec
        class P:
            pass

        fields = P._provider_metadata["fields"]
        tg_names = [f["name"] for f in fields["text_generation"]]
        emb_names = [f["name"] for f in fields["embedding"]]
        assert "contextLength" in tg_names
        assert "contextLength" not in emb_names

    def test_with_icon_and_color(self):
        dec = (
            AIModelProviderBuilder("Styled", "styled")
            .with_capabilities([ModelCapability.TEXT_GENERATION])
            .with_icon("/my/icon.svg")
            .with_color("#FF0000")
            .popular()
            .build_decorator()
        )

        @dec
        class P:
            pass

        meta = P._provider_metadata
        assert meta["iconPath"] == "/my/icon.svg"
        assert meta["color"] == "#FF0000"
        assert meta["isPopular"] is True

    def test_default_provider_id_from_name(self):
        builder = AIModelProviderBuilder("Azure OpenAI")
        assert builder._provider_id == "azureOpenai"


# ---------------------------------------------------------------------------
# TestAIModelRegistry
# ---------------------------------------------------------------------------

class TestAIModelRegistry:

    def setup_method(self):
        self.registry = AIModelRegistry()

    def test_register_provider(self):
        cls = _make_provider_class()
        assert self.registry.register(cls) is True
        assert len(self.registry.list_providers()) == 1

    def test_rejects_undecorated_class(self):
        class Plain:
            pass
        assert self.registry.register(Plain) is False
        assert len(self.registry.list_providers()) == 0

    def test_list_providers(self):
        cls1 = _make_provider_class(name="A", provider_id="a")
        cls2 = _make_provider_class(name="B", provider_id="b")
        self.registry.register(cls1)
        self.registry.register(cls2)
        providers = self.registry.list_providers()
        ids = [p["providerId"] for p in providers]
        assert "a" in ids
        assert "b" in ids

    def test_get_provider_exists(self):
        cls = _make_provider_class(provider_id="testP")
        self.registry.register(cls)
        p = self.registry.get_provider("testP")
        assert p is not None
        assert p["providerId"] == "testP"

    def test_get_provider_not_found(self):
        assert self.registry.get_provider("nonexistent") is None

    def test_get_provider_schema_returns_all_fields(self):
        fields = {"text_generation": [AIModelField(name="model", display_name="Model")]}
        cls = _make_provider_class(provider_id="s1", fields=fields)
        self.registry.register(cls)
        schema = self.registry.get_provider_schema("s1")
        assert schema is not None
        assert "text_generation" in schema["fields"]

    def test_get_provider_schema_filter_by_capability(self):
        fields = {
            "text_generation": [AIModelField(name="model", display_name="Model")],
            "embedding": [AIModelField(name="dim", display_name="Dimensions")],
        }
        cls = _make_provider_class(
            provider_id="s2",
            capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING],
            fields=fields,
        )
        self.registry.register(cls)
        schema = self.registry.get_provider_schema("s2", capability="embedding")
        assert "embedding" in schema["fields"]
        assert len(schema["fields"]) == 1

    def test_get_provider_schema_not_found(self):
        assert self.registry.get_provider_schema("nope") is None

    def test_get_capabilities(self):
        caps = self.registry.get_capabilities()
        assert "text_generation" in caps
        assert "embedding" in caps

    def test_filter_by_capability(self):
        cls1 = _make_provider_class(
            name="LLMOnly",
            provider_id="llmOnly",
            capabilities=[ModelCapability.TEXT_GENERATION],
        )
        cls2 = _make_provider_class(
            name="EmbOnly",
            provider_id="embOnly",
            capabilities=[ModelCapability.EMBEDDING],
        )
        self.registry.register(cls1)
        self.registry.register(cls2)
        llm_providers = self.registry.filter_by_capability("text_generation")
        assert len(llm_providers) == 1
        assert llm_providers[0]["providerId"] == "llmOnly"

    def test_search(self):
        cls = _make_provider_class(name="OpenAI", provider_id="openAI", description="GPT models")
        self.registry.register(cls)
        results = self.registry.search("openai")
        assert len(results) == 1
        results2 = self.registry.search("gpt")
        assert len(results2) == 1
        results3 = self.registry.search("zzz_nomatch")
        assert len(results3) == 0


# ---------------------------------------------------------------------------
# TestAIModelRegistryEdgeCases
# ---------------------------------------------------------------------------

class TestAIModelRegistryEdgeCases:

    def test_duplicate_registration_overwrites(self):
        registry = AIModelRegistry()
        cls1 = _make_provider_class(name="V1", provider_id="dup", description="version 1")
        cls2 = _make_provider_class(name="V2", provider_id="dup", description="version 2")
        registry.register(cls1)
        registry.register(cls2)
        assert len(registry.list_providers()) == 1
        assert registry.get_provider("dup")["description"] == "version 2"

    def test_empty_registry(self):
        registry = AIModelRegistry()
        assert registry.list_providers() == []
        assert registry.search("anything") == []
        assert registry.filter_by_capability("text_generation") == []

    def test_provider_no_fields_returns_empty(self):
        registry = AIModelRegistry()
        cls = _make_provider_class(provider_id="nofields")
        registry.register(cls)
        schema = registry.get_provider_schema("nofields", capability="text_generation")
        assert schema["fields"]["text_generation"] == []


class TestDefaultProviderId:

    def test_single_word(self):
        assert _default_provider_id("OpenAI") == "openAI"

    def test_multi_word(self):
        assert _default_provider_id("Azure OpenAI") == "azureOpenai"

    def test_empty(self):
        assert _default_provider_id("") == ""
