"""Tests for the AI model registry API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.api.routes.ai_models_registry import router
from app.config.ai_models.registry import ai_model_registry


def _client_as(user: dict) -> TestClient:
    """The registry router behind a stand-in for the auth middleware, which in
    query_main always sets request.state.user before any route runs."""
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.user = user
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def client():
    return _client_as({"userId": "user-1", "orgId": "org-1", "token_type": "regular"})


class TestTokenPolicy:

    def test_service_tokens_are_refused(self):
        service_client = _client_as({"token_type": "scoped", "scopes": ["fetch:config"]})
        assert service_client.get("/api/v1/ai-models/registry").status_code == 403


class TestGetRegistry:

    def test_returns_all_providers(self, client):
        resp = client.get("/api/v1/ai-models/registry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert isinstance(data["providers"], list)
        assert data["total"] >= 20
        for p in data["providers"]:
            assert "providerId" in p
            assert "name" in p
            assert "capabilities" in p

    def test_search_filter(self, client):
        resp = client.get("/api/v1/ai-models/registry", params={"search": "openai"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for p in data["providers"]:
            haystack = f"{p['name']} {p['providerId']} {p['description']}".lower()
            assert "openai" in haystack

    def test_capability_filter(self, client):
        resp = client.get("/api/v1/ai-models/registry", params={"capability": "embedding"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for p in data["providers"]:
            assert "embedding" in p["capabilities"]

    def test_search_and_capability_combined(self, client):
        resp = client.get(
            "/api/v1/ai-models/registry",
            params={"search": "openai", "capability": "embedding"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for p in data["providers"]:
            assert "embedding" in p["capabilities"]

    def test_search_no_results(self, client):
        resp = client.get(
            "/api/v1/ai-models/registry",
            params={"search": "zzz_definitely_not_a_provider"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestGetCapabilities:

    def test_returns_capabilities(self, client):
        resp = client.get("/api/v1/ai-models/registry/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        caps = data["capabilities"]
        assert isinstance(caps, list)
        assert len(caps) >= 2
        cap_ids = [c["id"] for c in caps]
        assert "text_generation" in cap_ids
        assert "embedding" in cap_ids
        for c in caps:
            assert "id" in c
            assert "name" in c
            assert "modelType" in c


class TestGetProviderSchema:

    def test_valid_provider(self, client):
        resp = client.get("/api/v1/ai-models/registry/openAI/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["provider"]["providerId"] == "openAI"
        fields = data["schema"]["fields"]
        assert "text_generation" in fields
        assert "embedding" in fields

    def test_with_capability_filter(self, client):
        resp = client.get(
            "/api/v1/ai-models/registry/openAI/schema",
            params={"capability": "text_generation"},
        )
        assert resp.status_code == 200
        data = resp.json()
        fields = data["schema"]["fields"]
        assert "text_generation" in fields

    def test_not_found_provider(self, client):
        resp = client.get("/api/v1/ai-models/registry/nonExistentProvider/schema")
        assert resp.status_code == 404

    def test_schema_fields_have_required_keys(self, client):
        resp = client.get("/api/v1/ai-models/registry/openAI/schema")
        data = resp.json()
        for cap_fields in data["schema"]["fields"].values():
            for field in cap_fields:
                assert "name" in field
                assert "displayName" in field
                assert "fieldType" in field
                assert "required" in field
