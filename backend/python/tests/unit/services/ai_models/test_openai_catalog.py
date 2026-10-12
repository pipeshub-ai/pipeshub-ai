"""OpenAI-compatible catalog of configured models."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.openai_models import router
from app.services.ai_models.openai_catalog import find_openai_model, openai_model_list

_STORED = {
    "llm": [
        {
            "provider": "openAI",
            "modelKey": "llm-1",
            "updatedAt": 1_700_000_000_000,
            "configuration": {
                "model": "gpt-4o, gpt-4o-mini",
                "apiKey": "sk-live-secret",
                "endpoint": "https://api.openai.com/v1",
            },
        }
    ],
    "imageGeneration": [
        {
            "provider": "openAICompatible",
            "configuration": {"model": "gpt-image-1", "apiKey": "sk-image"},
        }
    ],
    "tts": [
        {"provider": "openAICompatible", "configuration": {"model": "tts-1", "apiKey": "sk-tts"}}
    ],
    "stt": [
        {"provider": "openAI", "configuration": {"model": "whisper-1", "apiKey": "sk-stt"}}
    ],
    "embedding": [
        {"provider": "openAI", "configuration": {"model": "text-embedding-3-small", "apiKey": "sk-emb"}}
    ],
    "modelRoles": {"indexing": {"modelType": "llm", "modelKey": "llm-1"}},
}


def test_list_splits_names_and_omits_secrets():
    listed = openai_model_list(_STORED)
    by_id = {item["id"]: item for item in listed["data"]}
    assert listed["object"] == "list"
    assert set(by_id) == {
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-image-1",
        "tts-1",
        "whisper-1",
        "text-embedding-3-small",
    }
    assert by_id["gpt-4o"]["owned_by"] == "openAI"
    assert by_id["gpt-4o"]["created"] == 1_700_000_000
    assert by_id["gpt-4o"]["task"] == "chat"
    assert by_id["gpt-image-1"]["task"] == "image"
    assert by_id["tts-1"]["task"] == "text-to-speech"
    assert by_id["whisper-1"]["task"] == "automatic-speech-recognition"
    assert by_id["text-embedding-3-small"]["task"] == "embedding"
    dumped = str(listed)
    assert "sk-live-secret" not in dumped
    assert "sk-image" not in dumped
    assert "api.openai.com" not in dumped


def test_missing_model_is_none():
    assert find_openai_model(_STORED, "nope") is None
    assert find_openai_model(None, "gpt-4o") is None
    assert find_openai_model(_STORED, "gpt-4o")["owned_by"] == "openAI"


def test_empty_config_is_an_empty_list():
    assert openai_model_list(None) == {"object": "list", "data": []}
    assert openai_model_list({}) == {"object": "list", "data": []}


class _Config:
    def __init__(self, stored):
        self.stored = stored

    async def get_config(self, _path, use_cache=True):
        return self.stored


def _app(stored):
    app = FastAPI()
    app.container = SimpleNamespace(config_service=lambda: _Config(stored))
    app.include_router(router, prefix="/v1")
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_routes_list_and_retrieve():
    app = _app(_STORED)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/v1/models")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == "gpt-4o"
        same = await client.get("/api/v1/models")
        assert same.status_code == 200
        one = await client.get("/v1/models/org/whisper-1")
        assert one.status_code == 404
        assert one.json()["error"]["code"] == "model_not_found"
        found = await client.get("/v1/models/whisper-1")
        assert found.status_code == 200
        assert found.json()["task"] == "automatic-speech-recognition"
        assert "sk-stt" not in found.text
