"""Tests for POST /api/v1/extract/classify endpoint."""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response
from jose import jwt

from app.api.middlewares.auth import authMiddleware
from app.api.routes.extraction import router as extraction_router
from app.models.blocks import BlocksContainer, SemanticMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _authenticated_as_indexing(request: Request) -> Request:
    request.state.user = {
        "token_type": "scoped",
        "scopes": ["document:classify"],
        "orgId": "org-123",
    }
    return request


def _build_app(document_extraction) -> FastAPI:
    app = FastAPI()
    app.state.document_extraction = document_extraction
    app.include_router(extraction_router)
    app.dependency_overrides[authMiddleware] = _authenticated_as_indexing
    return app


def _empty_bc_dict() -> dict:
    return {"blocks": [], "block_groups": []}


def _semantic_metadata_dict() -> dict:
    return {
        "departments": ["Engineering"],
        "languages": ["English"],
        "topics": ["Testing"],
        "summary": "A test document.",
        "categories": ["Technical"],
        "sub_category_level_1": "Software",
        "sub_category_level_2": "Python",
        "sub_category_level_3": "Testing",
    }


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_classify_success() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(
        return_value=MagicMock(
            departments=["Engineering"],
            languages=["English"],
            topics=["Testing"],
            summary="A test doc.",
            category="Technical",
            subcategories=MagicMock(level1="Software", level2="Python", level3="Testing"),
        )
    )

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={
            "block_container": _empty_bc_dict(),
            "org_id": "org-123",
            "departments": ["Engineering", "Finance"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["classification"] is not None
    mock_extraction.classify.assert_awaited_once()


def test_classify_empty_document_returns_none_classification() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["classification"] is None


def test_classify_uses_provided_departments() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    client.post(
        "/api/v1/extract/classify",
        json={
            "block_container": _empty_bc_dict(),
            "org_id": "org-123",
            "departments": ["Engineering", "Finance"],
        },
    )

    call_kwargs = mock_extraction.classify.call_args[1]
    assert call_kwargs["departments"] == ["Engineering", "Finance"]


def test_classify_forwards_record_name_and_type() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    client.post(
        "/api/v1/extract/classify",
        json={
            "block_container": _empty_bc_dict(),
            "org_id": "org-123",
            "record_name": "Q3 Board Deck.pdf",
            "record_type": "FILE",
        },
    )

    call_kwargs = mock_extraction.classify.call_args[1]
    assert call_kwargs["record_name"] == "Q3 Board Deck.pdf"
    assert call_kwargs["record_type"] == "FILE"


def test_classify_record_name_and_type_default_to_empty() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    call_kwargs = mock_extraction.classify.call_args[1]
    assert call_kwargs["record_name"] == ""
    assert call_kwargs["record_type"] == ""


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_classify_llm_failure_returns_500() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "LLM timeout" in body["error"]


def test_classify_invalid_block_container_returns_422() -> None:
    mock_extraction = MagicMock()

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": "not-a-dict", "org_id": "org-123"},
    )

    assert response.status_code in {422, 500}


# ---------------------------------------------------------------------------
# Service-token enforcement (real signed tokens, real auth middleware)
# ---------------------------------------------------------------------------

JWT_SECRET = "session-secret-for-tests"
SCOPED_SECRET = "scoped-secret-for-tests"


class _SecretsConfigService:
    async def get_config(self, key, **kwargs):
        return {"jwtSecret": JWT_SECRET, "scopedJwtSecret": SCOPED_SECRET}


def _service_token(scope: str, **claims) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now, "exp": now + 3600, "scopes": [scope], **claims},
        SCOPED_SECRET,
        algorithm="HS256",
    )


def _real_auth_client(document_extraction: MagicMock) -> TestClient:
    app = _build_app(document_extraction)
    app.dependency_overrides.clear()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test.extraction_routes.auth"),
        config_service=_SecretsConfigService,
    )
    return TestClient(app)


def _classifier() -> MagicMock:
    document_extraction = MagicMock()
    document_extraction.classify = AsyncMock(return_value=None)
    return document_extraction


def _post_classify(client: TestClient, token: str | None, org_id: str | None = None) -> Response:
    body: dict = {"block_container": _empty_bc_dict()}
    if org_id is not None:
        body["org_id"] = org_id
    return client.post(
        "/api/v1/extract/classify",
        json=body,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


def test_classify_without_token_is_401_and_does_not_classify() -> None:
    document_extraction = _classifier()

    response = _post_classify(_real_auth_client(document_extraction), token=None, org_id="org-123")

    assert response.status_code == 401
    document_extraction.classify.assert_not_awaited()


def test_classify_with_parse_scope_is_403() -> None:
    document_extraction = _classifier()
    token = _service_token("document:parse", orgId="org-123")

    response = _post_classify(_real_auth_client(document_extraction), token, org_id="org-123")

    assert response.status_code == 403
    document_extraction.classify.assert_not_awaited()


def test_classify_uses_token_org_when_body_org_absent() -> None:
    document_extraction = _classifier()
    token = _service_token("document:classify", orgId="org-123")

    response = _post_classify(_real_auth_client(document_extraction), token)

    assert response.status_code == 200
    assert document_extraction.classify.call_args[1]["org_id"] == "org-123"


def test_classify_body_org_mismatch_is_403_and_does_not_classify() -> None:
    document_extraction = _classifier()
    token = _service_token("document:classify", orgId="org-123")

    response = _post_classify(_real_auth_client(document_extraction), token, org_id="org-other")

    assert response.status_code == 403
    assert response.json()["detail"] == "org_id does not match the service token"
    document_extraction.classify.assert_not_awaited()


def test_classify_token_without_org_is_401() -> None:
    document_extraction = _classifier()
    token = _service_token("document:classify")

    response = _post_classify(_real_auth_client(document_extraction), token, org_id="org-123")

    assert response.status_code == 401
    assert response.json()["detail"] == "Token missing orgId"
    document_extraction.classify.assert_not_awaited()
