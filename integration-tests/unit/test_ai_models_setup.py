"""How the test embedding model is put in place on a stack other sessions share.

PipesHub refuses to change or delete the embedding model while the vector store
holds vectors from it. The fixture must therefore reuse a model the org already
embeds with, say plainly when another model holds the store, and not fail its
teardown on the refusal.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from helper import ai_models_setup as setup

pytestmark = pytest.mark.unit

_IN_USE = (
    '{"error":{"status":"error","message":"This model is embedding your indexed content. '
    'Delete the embeddings in Labs first, then change or delete the model and re-embed."}}'
)
_ENDPOINT = "https://example.openai.azure.com"
_DEPLOYMENT = "embed-deployment"


class _Client:
    timeout_seconds = 5
    _access_token = "token"

    def _ensure_access_token(self) -> None:
        return None

    def _url(self, path: str) -> str:
        return f"http://pipeshub.test{path}"


class _Response:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _Backend:
    """Records the calls the helper makes; answers from what each test sets."""

    def __init__(self, configured: list[dict[str, Any]], post: _Response) -> None:
        self.configured = configured
        self.post_response = post
        self.delete_response = _Response(200, {})
        self.posts: list[dict[str, Any]] = []
        self.deletes: list[str] = []

    def get(self, url: str, **_: Any) -> _Response:
        assert url.endswith("/ai-models/embedding"), url
        return _Response(200, {"models": self.configured})

    def post(self, url: str, json: dict[str, Any], **_: Any) -> _Response:
        self.posts.append(json)
        return self.post_response

    def delete(self, url: str, **_: Any) -> _Response:
        self.deletes.append(url)
        return self.delete_response


def _entry(provider: str = "azureOpenAI", model: str = "text-embedding-3-small") -> dict[str, Any]:
    """A list entry as GET /ai-models/embedding returns it: configuration cut to
    the public keys (AI_PUBLIC_CONFIG_KEYS), so no endpoint or deployment."""
    return {
        "provider": provider,
        "configuration": {"model": model},
        "isDefault": True,
        "modelKey": "existing-key",
    }


@pytest.fixture(autouse=True)
def _azure_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TEST_AI_MODEL_PROVIDER",
        "TEST_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        "TEST_OLLAMA_ENDPOINT",
        "TEST_AZURE_OPENAI_DEPLOYMENT_NAME",
        "TEST_AZURE_OPENAI_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("TEST_AZURE_OPENAI_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("TEST_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", _DEPLOYMENT)


def _install(monkeypatch: pytest.MonkeyPatch, backend: _Backend) -> None:
    monkeypatch.setattr(setup.requests, "get", backend.get)
    monkeypatch.setattr(setup.requests, "post", backend.post)
    monkeypatch.setattr(setup.requests, "delete", backend.delete)


def test_the_model_the_org_already_embeds_with_is_reused_and_left_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend([_entry()], post=_Response(500, text="must not be called"))
    _install(monkeypatch, backend)

    seeded = setup.setup_test_embedding_model(_Client())
    setup.teardown_test_embedding_model(_Client(), seeded)

    assert (seeded.model_key, seeded.owned) == ("existing-key", False)
    assert backend.posts == []
    assert backend.deletes == []


def test_the_same_model_from_another_provider_is_added_rather_than_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend(
        [_entry(provider="openAI")],
        post=_Response(200, {"details": {"modelKey": "new-key"}}),
    )
    _install(monkeypatch, backend)

    seeded = setup.setup_test_embedding_model(_Client())

    assert (seeded.model_key, seeded.owned) == ("new-key", True)
    assert len(backend.posts) == 1


def test_a_store_held_by_another_model_fails_once_and_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_OPENAI_API_KEY", "openai-key")
    other = {**_entry(provider="openAI", model="text-embedding-3-large"), "modelKey": "other-key"}
    backend = _Backend([other], post=_Response(400, text=_IN_USE))
    _install(monkeypatch, backend)

    with pytest.raises(RuntimeError) as raised:
        setup.setup_test_embedding_model(_Client())

    assert len(backend.posts) == 1, "every other provider would be refused for the same reason"
    message = str(raised.value)
    assert "openAI text-embedding-3-large" in message
    assert "Labs" in message


def test_with_no_model_configured_the_refusal_names_the_built_in_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend([], post=_Response(400, text=_IN_USE))
    _install(monkeypatch, backend)

    with pytest.raises(RuntimeError, match="built-in model"):
        setup.setup_test_embedding_model(_Client())


def test_an_added_model_is_deleted_and_a_refused_delete_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend([], post=_Response(200, {"details": {"modelKey": "new-key"}}))
    _install(monkeypatch, backend)
    seeded = setup.setup_test_embedding_model(_Client())
    backend.delete_response = _Response(400, text=_IN_USE)

    setup.teardown_test_embedding_model(_Client(), seeded)

    assert backend.deletes == [
        "http://pipeshub.test/api/v1/configurationManager/ai-models/providers/embedding/new-key"
    ]


def test_ownership_survives_the_handoff_between_xdist_workers() -> None:
    seeded = setup.SeededAIModel("embedding", "azureOpenAI", "m", "k", owned=False)

    assert setup.SeededAIModel(**asdict(seeded)) == seeded
