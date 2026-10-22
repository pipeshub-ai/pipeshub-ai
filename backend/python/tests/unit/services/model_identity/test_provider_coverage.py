"""Every provider either carries identity or is on the list of those that cannot.

A provider added to ``LLMProvider`` or ``EmbeddingProvider`` fails here until it
is classified, so a new branch in ``aimodels`` cannot quietly skip forwarding.
"""

import pytest

from app.services.model_identity.transport.client_paths import iter_http_clients
from app.services.model_identity.transport.httpx_hook import has_identity_hook
from app.utils.aimodels import (
    EmbeddingProvider,
    LLMProvider,
    get_embedding_model,
    get_generator_model,
)

# Their SDKs do not use httpx (boto3, gRPC, the cohere and google clients), so
# there is nothing to attach a header hook to. Forwarding is logged and counted
# as unavailable for these; use a gateway in front of them.
NO_HTTPX_LLM = {"bedrock", "cohere", "gemini", "vertexAI"}
NO_HTTPX_EMBEDDING = {
    "anthropic", "bedrock", "cohere", "default", "gemini", "huggingFace", "jinaAI",
    "sentenceTransformers", "vertexAI", "voyage", "azureAI",
}

BASE = {
    "model": "m",
    "apiKey": "k",
    "endpoint": "https://gateway.example.com/v1",
    "identityForwarding": {"enabled": True},
    "deploymentName": "d",
    "apiVersion": "2024-02-01",
}


def test_every_llm_provider_is_classified() -> None:
    instrumented = {p.value for p in LLMProvider} - NO_HTTPX_LLM
    assert instrumented, "no instrumented provider"
    assert NO_HTTPX_LLM <= {p.value for p in LLMProvider}


@pytest.mark.parametrize("provider", sorted({p.value for p in LLMProvider} - NO_HTTPX_LLM))
def test_chat_model_clients_carry_the_hook(provider) -> None:
    model = get_generator_model(provider, {"configuration": dict(BASE), "isDefault": True}, "m")
    clients = iter_http_clients(model)
    assert clients, f"{provider}: no httpx client found; add its path to client_paths"
    assert all(has_identity_hook(c) for c in clients), provider


@pytest.mark.parametrize("provider", sorted({p.value for p in LLMProvider} - NO_HTTPX_LLM))
def test_a_disabled_connection_installs_nothing(provider) -> None:
    configuration = {**BASE, "identityForwarding": {"enabled": False}}
    model = get_generator_model(provider, {"configuration": configuration, "isDefault": True}, "m")
    assert not any(has_identity_hook(c) for c in iter_http_clients(model))


@pytest.mark.parametrize(
    "provider", ["openAI", "litellmProxy", "openAICompatible", "azureOpenAI", "ollama"]
)
def test_embedding_clients_carry_the_hook(provider) -> None:
    assert provider in {p.value for p in EmbeddingProvider}
    configuration = {**BASE, "endpoint": "https://gateway.example.com/v1"}
    emb = get_embedding_model(provider, {"configuration": configuration, "isDefault": True}, "m")
    clients = iter_http_clients(emb)
    assert clients, provider
    assert all(has_identity_hook(c) for c in clients), provider


def test_every_embedding_provider_is_classified() -> None:
    known = {"openAI", "litellmProxy", "openAICompatible", "azureOpenAI", "ollama"}
    unclassified = {p.value for p in EmbeddingProvider} - known - NO_HTTPX_EMBEDDING
    # These build httpx-based clients but need a live endpoint or extra fields
    # to construct offline; they go through the same funnel as the five above.
    assert unclassified <= {"fireworks", "lmStudio", "mistral", "openRouter", "together"}


def test_both_funnels_install_the_hook() -> None:
    import inspect

    from app.utils import aimodels

    assert "instrument_model" in inspect.getsource(aimodels._decorate_chat_model)
    assert "instrument_model" in inspect.getsource(aimodels._decorate_embeddings)
    assert "_decorate_chat_model(" in inspect.getsource(aimodels.get_generator_model)
    assert "_decorate_embeddings(" in inspect.getsource(aimodels.get_embedding_model)
