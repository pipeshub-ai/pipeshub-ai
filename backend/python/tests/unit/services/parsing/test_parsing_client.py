"""Tests for ParsingClient HTTP client."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.models.blocks import BlocksContainer
from app.services.base_client import ServiceCallError, ServiceUnavailableError
from app.services.parsing.client import ParsingClient, ParsingClientError
from app.services.parsing.interface import ParseErrorCode, ParseResult, ParserProvider


def _bc_dict() -> dict:
    return {"blocks": [], "block_groups": []}


def _success_response() -> dict:
    return {
        "success": True,
        "block_container": _bc_dict(),
        "provider_used": "default",
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = iter(responses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return next(self._responses)


def _make_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_success() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(200, _success_response())),
    ):
        result = await client.parse(
            file_content=b"data",
            record_name="test.csv",
            mime_type="text/csv",
            extension="csv",
        )

    assert isinstance(result, ParseResult)
    assert result.provider_used == ParserProvider.DEFAULT


@pytest.mark.asyncio
async def test_parse_with_explicit_provider() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    success_body = {**_success_response(), "provider_used": "docling"}

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(200, success_body)),
    ):
        result = await client.parse(
            file_content=b"data",
            record_name="test.pdf",
            mime_type="application/pdf",
            provider=ParserProvider.DOCLING,
        )

    assert result.provider_used == ParserProvider.DOCLING


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_raises_parsing_client_error_on_service_failure() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    error_body = {
        "success": False,
        "error": {
            "code": "PARSE_FAILED",
            "message": "Docling crashed",
            "details": {},
        },
    }

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(return_value=_make_response(500, error_body)),
    ):
        with pytest.raises(ParsingClientError) as exc_info:
            await client.parse(file_content=b"data", record_name="test.pdf")

    assert exc_info.value.code == ParseErrorCode.PARSE_FAILED


@pytest.mark.asyncio
async def test_parse_raises_service_unavailable_on_connection_error() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1, retry_delay=0.0)

    with patch.object(
        client,
        "_post_multipart",
        new=AsyncMock(side_effect=ServiceUnavailableError("Connection refused", service_name="ParsingService")),
    ):
        with pytest.raises(ServiceUnavailableError):
            await client.parse(file_content=b"data", record_name="test.pdf")


# ---------------------------------------------------------------------------
# list_providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_providers_success() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    providers_body = {"pdf": ["docling", "default"], "csv": ["default"]}

    with patch.object(
        client,
        "_get_json",
        new=AsyncMock(return_value=_make_response(200, providers_body)),
    ):
        result = await client.list_providers()

    assert "pdf" in result
    assert "default" in result["csv"]


@pytest.mark.asyncio
async def test_health_check_returns_true_on_ok() -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.get = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))
        mock_cls.return_value = mock_instance

        result = await client.health_check()

    assert result is True
