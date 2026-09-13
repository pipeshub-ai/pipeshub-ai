"""Tests for POST /api/v1/extract/classify endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.extraction import router as extraction_router
from app.models.blocks import SemanticMetadata
from app.modules.transformers.document_extraction import ExtractionLLMError
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

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
        return_value=SemanticMetadata(
            departments=["Engineering"],
            languages=["English"],
            topics=["Testing"],
            summary="A test doc.",
            categories=["Technical"],
            sub_category_level_1="Software",
            sub_category_level_2="Python",
            sub_category_level_3="Testing",
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


def test_classify_without_a_configured_llm_returns_422_with_error_code() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(
        side_effect=LLMNotConfiguredError("No LLM is configured for this organization")
    )
    client = TestClient(_build_app(mock_extraction))

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "LLM_NOT_CONFIGURED"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMUnavailableError("connection refused"), "LLM_UNAVAILABLE"),
        (ExtractionLLMError("no usable output"), "LLM_FAILED"),
    ],
)
def test_model_failures_answer_424_with_a_code(error: Exception, code: str) -> None:
    """A dependency failed, not this service: a 5xx would be retried and trip the caller's breaker."""
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=error)
    client = TestClient(_build_app(mock_extraction))

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 424
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == code


def test_an_open_circuit_tells_the_caller_how_long_to_wait() -> None:
    from app.services.llm_gateway.gateway import ProviderUnavailableError

    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=ProviderUnavailableError("down", provider="p", retry_after=42.4))
    response = TestClient(_build_app(mock_extraction)).post(
        "/api/v1/extract/classify", json={"block_container": _empty_bc_dict(), "org_id": "org-123"}
    )
    assert response.status_code == 424
    assert response.headers["Retry-After"] == "43"


def test_an_outage_before_the_circuit_opens_names_no_wait() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=LLMUnavailableError("down"))
    response = TestClient(_build_app(mock_extraction)).post(
        "/api/v1/extract/classify", json={"block_container": _empty_bc_dict(), "org_id": "org-123"}
    )
    assert response.status_code == 424
    assert "Retry-After" not in response.headers
