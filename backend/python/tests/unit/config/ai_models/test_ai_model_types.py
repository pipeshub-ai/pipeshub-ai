"""Tests for app.config.ai_models.types."""

import pytest

from app.config.ai_models.types import (
    AIModelField,
    CAPABILITY_TO_MODEL_TYPE,
    MODEL_TYPE_TO_CAPABILITY,
    ModelCapability,
)


class TestModelCapability:
    """Tests for the ModelCapability enum."""

    def test_is_str_enum(self):
        assert isinstance(ModelCapability.TEXT_GENERATION, str)

    def test_text_generation_value(self):
        assert ModelCapability.TEXT_GENERATION == "text_generation"

    def test_embedding_value(self):
        assert ModelCapability.EMBEDDING == "embedding"

    def test_all_values_are_snake_case(self):
        for cap in ModelCapability:
            assert cap.value == cap.value.lower()
            assert " " not in cap.value

    def test_at_least_two_capabilities(self):
        assert len(ModelCapability) >= 2


class TestAIModelField:
    """Tests for the AIModelField dataclass."""

    def test_defaults(self):
        f = AIModelField(name="test", display_name="Test")
        assert f.field_type == "TEXT"
        assert f.required is True
        assert f.default_value == ""
        assert f.placeholder == ""
        assert f.description == ""
        assert f.is_secret is False
        assert f.options == []
        assert f.validation == {}

    def test_all_fields_constructor(self):
        f = AIModelField(
            name="apiKey",
            display_name="API Key",
            field_type="PASSWORD",
            required=True,
            default_value="",
            placeholder="Your API Key",
            description="Enter your key",
            is_secret=True,
            options=[{"value": "a", "label": "A"}],
            validation={"minLength": 1},
        )
        assert f.name == "apiKey"
        assert f.is_secret is True
        assert f.options == [{"value": "a", "label": "A"}]

    def test_to_dict_minimal(self):
        f = AIModelField(name="model", display_name="Model")
        d = f.to_dict()
        assert d["name"] == "model"
        assert d["displayName"] == "Model"
        assert d["fieldType"] == "TEXT"
        assert d["required"] is True
        assert "placeholder" not in d
        assert "isSecret" not in d

    def test_to_dict_full(self):
        f = AIModelField(
            name="apiKey",
            display_name="API Key",
            field_type="PASSWORD",
            required=True,
            default_value="default_val",
            placeholder="Enter key",
            description="A secret key",
            is_secret=True,
            options=[{"value": "x", "label": "X"}],
            validation={"minLength": 5},
        )
        d = f.to_dict()
        assert d["isSecret"] is True
        assert d["placeholder"] == "Enter key"
        assert d["description"] == "A secret key"
        assert d["defaultValue"] == "default_val"
        assert d["options"] == [{"value": "x", "label": "X"}]
        assert d["validation"] == {"minLength": 5}

    def test_to_dict_omits_empty_optional_fields(self):
        f = AIModelField(name="x", display_name="X")
        d = f.to_dict()
        assert "options" not in d
        assert "validation" not in d
        assert "isSecret" not in d


class TestCapabilityMappings:
    """Tests for the mapping constants."""

    def test_text_generation_maps_to_llm(self):
        assert CAPABILITY_TO_MODEL_TYPE["text_generation"] == "llm"

    def test_embedding_maps_to_embedding(self):
        assert CAPABILITY_TO_MODEL_TYPE["embedding"] == "embedding"

    def test_reverse_mapping_roundtrip(self):
        for cap, mt in CAPABILITY_TO_MODEL_TYPE.items():
            assert MODEL_TYPE_TO_CAPABILITY[mt] == cap

    def test_all_capabilities_mapped(self):
        for cap in [ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING, ModelCapability.OCR]:
            assert cap.value in CAPABILITY_TO_MODEL_TYPE
