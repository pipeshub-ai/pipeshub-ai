"""Tests for POST /api/v1/parse and GET /api/v1/parse/providers endpoints."""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.parsing import router as parsing_router
from app.models.blocks import BlocksContainer
from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.registry import ParserRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(registry: ParserRegistry) -> FastAPI:
    app = FastAPI()
    app.state.parser_registry = registry
    app.include_router(parsing_router)
    return app


def _empty_bc() -> BlocksContainer:
    return BlocksContainer(blocks=[], block_groups=[])


def _ok_result() -> ParseResult:
    return ParseResult(
        block_container=_empty_bc(),
        provider_used=ParserProvider.DEFAULT,
        metadata={},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/parse — success
# ---------------------------------------------------------------------------


def test_parse_endpoint_success() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(return_value=_ok_result())
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.csv", b"a,b\n1,2", "text/csv")},
        data={"record_name": "test.csv", "mime_type": "text/csv", "extension": "csv", "provider": "default"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["provider_used"] == "default"
    mock_parser.parse.assert_awaited_once()


def test_parse_endpoint_with_explicit_provider() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(return_value=_ok_result())
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        data={
            "record_name": "test.pdf",
            "mime_type": "application/pdf",
            "extension": "pdf",
            "provider": "default",
        },
    )

    assert response.status_code == 200
    registry.resolve.assert_called_once()
    mock_parser.parse.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/parse — error cases
# ---------------------------------------------------------------------------


def test_parse_endpoint_no_provider_returns_400() -> None:
    registry = MagicMock(spec=ParserRegistry)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.xyz", b"data", "application/octet-stream")},
        data={"mime_type": "application/octet-stream", "extension": "xyz"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NO_PROVIDER_PROVIDED"


def test_parse_endpoint_unsupported_format() -> None:
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(
        side_effect=ParseError(ParseErrorCode.UNSUPPORTED_FORMAT, "No parser for xyz")
    )

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.xyz", b"data", "application/octet-stream")},
        data={"mime_type": "application/octet-stream", "extension": "xyz", "provider": "default"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_parse_endpoint_parse_failed() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(
        side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "Docling crashed")
    )
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"bad data", "application/pdf")},
        data={"mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PARSE_FAILED"


def test_parse_endpoint_invalid_provider() -> None:
    registry = MagicMock(spec=ParserRegistry)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"data", "application/pdf")},
        data={"mime_type": "application/pdf", "provider": "nonexistent_provider"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False


def test_parse_endpoint_unexpected_exception() -> None:
    registry = MagicMock(spec=ParserRegistry)
    mock_parser = MagicMock()
    mock_parser.parse = AsyncMock(side_effect=RuntimeError("Boom"))
    registry.resolve = MagicMock(return_value=mock_parser)

    app = _build_app(registry)
    client = TestClient(app)

    response = client.post(
        "/api/v1/parse",
        files={"file": ("test.pdf", b"data", "application/pdf")},
        data={"mime_type": "application/pdf", "provider": "default"},
    )

    assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/v1/parse/providers
# ---------------------------------------------------------------------------


def test_list_providers_endpoint() -> None:
    registry = MagicMock(spec=ParserRegistry)
    registry.list_all_formats.return_value = {
        "pdf": ["docling", "default"],
        "csv": ["default"],
    }

    app = _build_app(registry)
    client = TestClient(app)

    response = client.get("/api/v1/parse/providers")

    assert response.status_code == 200
    body = response.json()
    assert "pdf" in body
    assert "csv" in body
    assert "docling" in body["pdf"]
