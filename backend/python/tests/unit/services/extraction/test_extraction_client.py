"""Tests for ExtractionClient HTTP client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.config.constants.service import TokenScopes
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


@pytest.mark.asyncio
async def test_classify_passes_record_name_and_type_in_request() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}
    mock_post = AsyncMock(return_value=_make_response(200, response_body))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(
            _bc(), "org-456", record_name="Q3 Board Deck.pdf", record_type="FILE",
        )

    call_args = mock_post.call_args
    payload = call_args[0][1]
    assert payload["record_name"] == "Q3 Board Deck.pdf"
    assert payload["record_type"] == "FILE"


@pytest.mark.asyncio
async def test_classify_record_name_and_type_default_to_empty_string() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)

    response_body = {"success": True, "classification": None}
    mock_post = AsyncMock(return_value=_make_response(200, response_body))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-456")

    call_args = mock_post.call_args
    payload = call_args[0][1]
    assert payload["record_name"] == ""
    assert payload["record_type"] == ""


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


# ---------------------------------------------------------------------------
# Service token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_passes_org_for_token() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    mock_post = AsyncMock(return_value=_make_response(200, {"success": True, "classification": None}))

    with patch.object(client, "_post_json", new=mock_post):
        await client.classify(_bc(), "org-123")

    assert mock_post.await_args.kwargs["org_id"] == "org-123"


def test_extraction_client_uses_document_classify_scope() -> None:
    config_service = MagicMock()
    client = ExtractionClient(service_url="http://fake-extraction:8093", config_service=config_service)

    assert client._service_scope is TokenScopes.DOCUMENT_CLASSIFY
    assert client._config_service is config_service


def _extraction_body() -> dict:
    return {
        "status": "COMPLETED",
        "termination_reason": "finish_ok",
        "strategy": "agent",
        "entities": [
            {"kind": "organization", "display_name": "Acme", "norm_key": "org:acme"},
            {"kind": "url", "display_name": "https://acme.example", "norm_key": "url:https://acme.example"},
        ],
        "stats": {"units": 1},
    }


async def _extract(body: dict):
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, {"success": True, "extraction": body})),
    ):
        return await client.extract_entities(_bc(), "org-123")


@pytest.mark.asyncio
async def test_extract_entities_reads_a_newer_service_reply() -> None:
    body = _extraction_body()
    body["future_top"] = 1
    body["stats"]["future_stat"] = 2
    body["entities"][0]["future_entity"] = 3

    result = await _extract(body)

    assert result.status == "COMPLETED"
    assert [entity.display_name for entity in result.entities] == ["Acme", "https://acme.example"]


@pytest.mark.asyncio
async def test_extract_entities_drops_only_the_entity_of_an_unknown_kind() -> None:
    body = _extraction_body()
    body["entities"][0]["kind"] = "vehicle"

    result = await _extract(body)

    assert [entity.display_name for entity in result.entities] == ["https://acme.example"]
    assert result.status == "PARTIAL"


@pytest.mark.asyncio
async def test_extract_entities_without_an_extraction_is_failed() -> None:
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    with patch.object(
        client,
        "_post_json",
        new=AsyncMock(return_value=_make_response(200, {"success": True, "extraction": None})),
    ):
        result = await client.extract_entities(_bc(), "org-123")

    assert (result.status, result.termination_reason) == ("FAILED", "llm_error")


@pytest.mark.asyncio
async def test_a_busy_extraction_service_is_waited_out_without_tripping_the_breaker() -> None:
    replies = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"success": False, "error": "busy"}),
        httpx.Response(200, json={"success": True, "extraction": _extraction_body()}),
    ]
    client = ExtractionClient(service_url="http://fake-extraction:8093", max_retries=1)
    client._auth_headers = AsyncMock(return_value={})  # type: ignore[method-assign]
    client._make_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(lambda request: replies.pop(0)),
    )
    with patch.object(client.circuit_breaker, "record_failure") as failure:
        result = await client.extract_entities(_bc(), "org-123")

    assert result.status == "COMPLETED"
    failure.assert_not_called()
