
import pytest

from app.utils.aimodels import is_local_cpu_embedding_provider


def test_none_provider_is_local() -> None:
    """Verify that an unconfigured provider is treated as local."""
    assert is_local_cpu_embedding_provider(
        None, "https://api.openai.com/v1"
    ) is True


@pytest.mark.parametrize(
    "provider",
    ["default", "huggingFace", "sentenceTransformers"],
)
def test_local_cpu_providers_are_local(provider: str) -> None:
    """Verify that providers designated as local are classified correctly."""
    assert (
        is_local_cpu_embedding_provider(provider, "https://api.openai.com/v1")
        is True
    )


@pytest.mark.parametrize(
    "provider",
    ["openAI", "voyage"],
)
def test_cloud_providers_remain_remote_on_local_endpoint(provider: str) -> None:
    """Verify that cloud providers remain remote even on a local endpoint."""
    assert (
        is_local_cpu_embedding_provider(provider, "http://127.0.0.1:8000")
        is False
    )


def test_provider_name_is_case_sensitive() -> None:
    """Verify that provider names are matched case-sensitively."""
    assert (
        is_local_cpu_embedding_provider("Ollama", "http://localhost:11434")
        is False
    )


@pytest.mark.parametrize(
    "endpoint",
    ["", None],
)
def test_ollama_without_endpoint_is_remote(endpoint: str | None) -> None:
    """Verify that Ollama without an endpoint is treated as remote."""
    assert is_local_cpu_embedding_provider("ollama", endpoint) is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://localhost.",
        "http://127.0.0.1:11434",
        "http://[::1]:8080",
        "http://10.1.2.3:11434",
        "http://embeddings.local/v1",
        "http://embed.svc:9",
        "http://embeddings.localhost/v1",
        "ollama:11434",
    ],
)
def test_ollama_with_local_endpoint_is_local(endpoint: str) -> None:
    """Verify that Ollama endpoints on local addresses are classified as local."""
    assert is_local_cpu_embedding_provider("ollama", endpoint) is True


@pytest.mark.parametrize(
    "endpoint",
    ["https://api.openai.com/v1", "http://8.8.8.8:11434"],
)
def test_ollama_with_remote_endpoint_is_remote(endpoint: str) -> None:
    """Verify that Ollama endpoints on remote addresses are classified as remote."""
    assert is_local_cpu_embedding_provider("ollama", endpoint) is False


@pytest.mark.parametrize(
    "provider",
    ["lmStudio", "litellmProxy", "openAICompatible"],
)
def test_self_hostable_providers_with_local_endpoint(provider: str) -> None:
    """Verify that self-hostable providers with local endpoints are local."""
    assert (
        is_local_cpu_embedding_provider(provider, "http://127.0.0.1:8000")
        is True
    )


@pytest.mark.parametrize(
    "provider",
    ["lmStudio", "litellmProxy", "openAICompatible"],
)
def test_self_hostable_providers_with_remote_endpoint(provider: str) -> None:
    """Verify that self-hostable providers with remote endpoints remain remote."""
    assert (
        is_local_cpu_embedding_provider(provider, "https://api.openai.com/v1")
        is False
    )
