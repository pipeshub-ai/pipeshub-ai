"""Tests for POST /api/v1/extract/classify endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.extraction import router as extraction_router
from app.models.blocks import BlocksContainer, SemanticMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(document_extraction) -> FastAPI:
    app = FastAPI()
    app.state.document_extraction = document_extraction
    app.include_router(extraction_router)
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
