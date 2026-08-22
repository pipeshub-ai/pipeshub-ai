"""Tests for ExtractionClient HTTP client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.models.blocks import BlocksContainer, SemanticMetadata
from app.services.base_client import ServiceUnavailableError
from app.services.extraction.client import ExtractionClient, ExtractionClientError


def _bc() -> BlocksContainer:
    return BlocksContainer(blocks=[], block_groups=[])


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


def _make_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_returns_semantic_metadata() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {
        "success": True,
        "classification": _semantic_metadata_dict(),
    }

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, response_body)),
    ):
        result = await client.classify(_bc(), "org-123", departments=["Engineering"])

    assert isinstance(result, SemanticMetadata)
    assert "Engineering" in result.departments


@pytest.mark.asyncio
async def test_classify_returns_none_for_empty_document() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, response_body)),
    ):
        result = await client.classify(_bc(), "org-123")

    assert result is None


@pytest.mark.asyncio
async def test_classify_passes_departments_in_request() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}
    mock_post = AsyncMock(return_value=_make_response(200, response_body))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-456", departments=["Finance", "HR"])

    call_args = mock_post.call_args
    payload = call_args[0][1]  # second positional arg = payload dict
    assert payload["departments"] == ["Finance", "HR"]
    assert payload["org_id"] == "org-456"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_raises_extraction_client_error_on_service_failure() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": False, "error": "LLM service unavailable"}

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(500, response_body)),
    ):
        with pytest.raises(ExtractionClientError) as exc_info:
            await client.classify(_bc(), "org-123")

    assert "LLM service unavailable" in exc_info.value.message


@pytest.mark.asyncio
async def test_classify_raises_service_unavailable_on_connection_error() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1, retry_delay=0.0)

    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(side_effect=ServiceUnavailableError("Connection refused")),
    ):
        with pytest.raises(ServiceUnavailableError):
            await client.classify(_bc(), "org-123")
