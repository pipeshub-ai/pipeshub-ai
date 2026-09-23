"""The reranking capability in the AI model registry, which drives the admin forms."""

from __future__ import annotations

from app.config.ai_models.providers import ai_model_registry
from app.config.ai_models.types import CAPABILITY_TO_MODEL_TYPE, ModelCapability
from app.config.constants.ai_models import DEFAULT_RERANKER_MODEL


def _field_names(provider_id: str, capability: str) -> list[str]:
    provider = ai_model_registry.get_provider(provider_id)
    return [f["name"] for f in provider["fields"][capability]]


def test_reranking_is_stored_under_the_reranker_model_type() -> None:
    assert CAPABILITY_TO_MODEL_TYPE[ModelCapability.RERANKING.value] == "reranker"


def test_the_system_default_reranker_needs_no_input() -> None:
    provider = ai_model_registry.get_provider("defaultReranker")
    assert provider["capabilities"] == ["reranking"]
    assert provider["modelName"] == DEFAULT_RERANKER_MODEL
    assert provider["fields"]["reranking"] == []


def test_reranker_forms_ask_only_for_what_a_reranker_needs() -> None:
    assert _field_names("cohere", "reranking") == ["apiKey", "model", "modelFriendlyName"]
    assert _field_names("openAICompatible", "reranking") == [
        "endpoint", "apiKey", "model", "modelFriendlyName",
    ]
    assert _field_names("huggingFace", "reranking") == ["model", "modelFriendlyName", "trustRemoteCode"]
    for provider in ai_model_registry.filter_by_capability("reranking"):
        names = {f["name"] for f in provider["fields"]["reranking"]}
        assert not names & {"dimensions", "isMultimodal", "contextLength", "isReasoning"}


def test_embedding_forms_are_unchanged_by_adding_reranking() -> None:
    assert _field_names("jinaAI", "embedding") == [
        "model", "apiKey", "modelFriendlyName", "dimensions", "isMultimodal",
    ]
    assert _field_names("sentenceTransformers", "embedding") == [
        "model", "modelFriendlyName", "dimensions", "isMultimodal", "trustRemoteCode",
    ]
    assert _field_names("cohere", "embedding") == [
        "apiKey", "model", "modelFriendlyName", "dimensions", "isMultimodal",
    ]
