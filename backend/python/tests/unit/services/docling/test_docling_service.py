"""Unit tests for app.services.docling.docling_service."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mocking to handle missing optional dependencies.
# docling_service imports FastAPI, docling, uvicorn which may not be installed
# in the test environment.
# ---------------------------------------------------------------------------

def _ensure_mock_modules():
    """Mock modules that may not be installed in the test environment."""
    # Create mock FastAPI module with required classes
    if "fastapi" not in sys.modules:
        mock_fastapi = MagicMock()
        # FastAPI app mock
        mock_app = MagicMock()
        mock_fastapi.FastAPI.return_value = mock_app
        mock_fastapi.HTTPException = type("HTTPException", (Exception,), {
            "__init__": lambda self, status_code=500, detail="": setattr(self, "status_code", status_code) or setattr(self, "detail", detail)
        })
        sys.modules["fastapi"] = mock_fastapi
        sys.modules["fastapi.testclient"] = MagicMock()

    if "uvicorn" not in sys.modules:
        sys.modules["uvicorn"] = MagicMock()

    if "docling_core" not in sys.modules:
        sys.modules["docling_core"] = MagicMock()
        sys.modules["docling_core.types"] = MagicMock()
        sys.modules["docling_core.types.doc"] = MagicMock()
        sys.modules["docling_core.types.doc.document"] = MagicMock()

    # Mock app.modules.parsers.pdf.docling_processor which may have heavy deps
    if "app.modules.parsers.pdf.docling_processor" not in sys.modules:
        sys.modules["app.modules.parsers.pdf.docling_processor"] = MagicMock()

    # Mock app.models.blocks which may not exist in test env
    if "app.models.blocks" not in sys.modules:
        mock_blocks = MagicMock()
        sys.modules["app.models.blocks"] = mock_blocks

    # Mock app.config.constants.http_status_code
    if "app.config.constants.http_status_code" not in sys.modules:
        mock_http = MagicMock()
        sys.modules["app.config.constants.http_status_code"] = mock_http

_ensure_mock_modules()

from app.services.docling.docling_service import (
    DoclingService,
    ParseResponse,
    serialize_docling_doc,
    set_docling_service,
)


def _make_upload_file(content: bytes = b"data"):
    """Create a mock UploadFile with async read()."""
    mock_file = MagicMock()
    mock_file.read = AsyncMock(return_value=content)
    return mock_file


# ---------------------------------------------------------------------------
# DoclingService
# ---------------------------------------------------------------------------

class TestDoclingServiceInit:
    def test_init_defaults(self):
        svc = DoclingService()
        assert svc.processor is None
        assert svc.config_service is None
        assert svc.logger is not None

    def test_init_with_logger(self):
        logger = MagicMock()
        svc = DoclingService(logger=logger)
        assert svc.logger is logger

    def test_init_with_config_service(self):
        cfg = MagicMock()
        svc = DoclingService(config_service=cfg)
        assert svc.config_service is cfg


class TestDoclingServiceInitialize:
    @pytest.mark.asyncio
    async def test_initialize_no_config_service_raises(self):
        svc = DoclingService()
        with pytest.raises(ValueError, match="Config service not provided"):
            await svc.initialize()

    @pytest.mark.asyncio
    @patch("app.services.docling.docling_service.DoclingProcessor")
    async def test_initialize_success(self, mock_processor_cls):
        cfg = MagicMock()
        svc = DoclingService(config_service=cfg)
        await svc.initialize()
        assert svc.processor is not None
        mock_processor_cls.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.docling.docling_service.DoclingProcessor", side_effect=RuntimeError("init fail"))
    async def test_initialize_processor_error(self, mock_processor_cls):
        cfg = MagicMock()
        svc = DoclingService(config_service=cfg)
        with pytest.raises(RuntimeError, match="init fail"):
            await svc.initialize()


class TestDoclingServiceHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self):
        svc = DoclingService()
        assert await svc.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_initialized(self):
        svc = DoclingService()
        svc.processor = MagicMock()
        assert await svc.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_exception(self):
        svc = DoclingService()
        # Simulate processor check raising
        svc.processor = MagicMock()
        # Override the property to raise
        type(svc).processor = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad")))
        result = await svc.health_check()
        assert result is False
        # Clean up
        type(svc).processor = None


class TestDoclingServiceParsePdfOnly:
    @pytest.mark.asyncio
    async def test_parse_pdf_not_initialized(self):
        svc = DoclingService()
        with pytest.raises(RuntimeError, match="not initialized"):
            await svc.parse_pdf_only("test.pdf", b"data")

    @pytest.mark.asyncio
    async def test_parse_pdf_success(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_doc = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_doc)
        svc.processor = mock_processor

        result = await svc.parse_pdf_only("test.pdf", b"data")
        assert result is mock_doc
        mock_processor.parse_document.assert_awaited_once_with(
            "test.pdf", b"data", page_range=None
        )

    @pytest.mark.asyncio
    async def test_parse_pdf_with_page_range(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_doc = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_doc)
        svc.processor = mock_processor

        result = await svc.parse_pdf_only("test.pdf", b"data", page_range=(1, 10))
        assert result is mock_doc
        mock_processor.parse_document.assert_awaited_once_with(
            "test.pdf", b"data", page_range=(1, 10)
        )

    @pytest.mark.asyncio
    async def test_parse_pdf_error(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(side_effect=ValueError("bad pdf"))
        svc.processor = mock_processor

        with pytest.raises(ValueError, match="bad pdf"):
            await svc.parse_pdf_only("test.pdf", b"data")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

class TestSerializeDoclingDoc:
    def test_success(self):
        doc = MagicMock()
        doc.model_dump_json.return_value = '{"pages": []}'
        result = serialize_docling_doc(doc)
        assert result == '{"pages": []}'

    def test_failure_raises_typeerror(self):
        doc = MagicMock()
        doc.model_dump_json.side_effect = Exception("oops")
        with pytest.raises(TypeError, match="Failed to serialize DoclingDocument"):
            serialize_docling_doc(doc)


# ---------------------------------------------------------------------------
# set_docling_service
# ---------------------------------------------------------------------------

class TestSetDoclingService:
    def test_sets_global(self):
        import app.services.docling.docling_service as mod
        svc = DoclingService()
        set_docling_service(svc)
        assert mod.docling_service is svc
        # Clean up
        mod.docling_service = None


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class TestRequestResponseModels:
    def test_parse_response(self):
        resp = ParseResponse(success=True, parse_result='{"doc": true}')
        assert resp.parse_result == '{"doc": true}'


# ---------------------------------------------------------------------------
# FastAPI endpoint functions (direct function calls, not via TestClient)
# ---------------------------------------------------------------------------


class TestStartupEvent:
    """Tests for the startup event handler."""

    @pytest.mark.asyncio
    async def test_startup_when_service_is_none(self):
        """When docling_service is None, startup should return silently."""
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        mod.docling_service = None
        try:
            from app.services.docling.docling_service import startup_event
            await startup_event()
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_startup_when_processor_is_none(self):
        """When processor is None, initialize() is called."""
        import app.services.docling.docling_service as mod
        svc = DoclingService()
        svc.initialize = AsyncMock()
        original = mod.docling_service
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import startup_event
            await startup_event()
            svc.initialize.assert_awaited_once()
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_startup_when_processor_already_set(self):
        """When processor is already set, initialize() is NOT called."""
        import app.services.docling.docling_service as mod
        svc = DoclingService()
        svc.processor = MagicMock()  # Already initialized
        svc.initialize = AsyncMock()
        original = mod.docling_service
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import startup_event
            await startup_event()
            svc.initialize.assert_not_awaited()
        finally:
            mod.docling_service = original


class TestHealthCheckEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from app.services.docling.docling_service import health_check
        result = await health_check()
        assert result == {"status": "healthy", "service": "docling"}


class TestParsePdfEndpoint:
    """Tests for the /parse-pdf endpoint (multipart file upload)."""

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_service_not_available(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        mod.docling_service = None
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await parse_pdf_endpoint(
                    file=_make_upload_file(b"data"),
                    record_name="test.pdf",
                    start_page=None,
                    end_page=None,
                )
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_success(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        svc = DoclingService()
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = '{"pages": []}'
        svc.parse_pdf_only = AsyncMock(return_value=mock_doc)
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            resp = await parse_pdf_endpoint(
                file=_make_upload_file(b"data"),
                record_name="test.pdf",
                start_page=None,
                end_page=None,
            )
            assert resp.success is True
            svc.parse_pdf_only.assert_awaited_once_with(
                "test.pdf", b"data", page_range=None
            )
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_with_page_range(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        svc = DoclingService()
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = '{"pages": []}'
        svc.parse_pdf_only = AsyncMock(return_value=mock_doc)
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            resp = await parse_pdf_endpoint(
                file=_make_upload_file(b"data"),
                record_name="test.pdf",
                start_page=5,
                end_page=15,
            )
            assert resp.success is True
            svc.parse_pdf_only.assert_awaited_once_with(
                "test.pdf", b"data", page_range=(5, 15)
            )
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_error(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        svc = DoclingService()
        svc.parse_pdf_only = AsyncMock(side_effect=RuntimeError("parse fail"))
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            resp = await parse_pdf_endpoint(
                file=_make_upload_file(b"data"),
                record_name="test.pdf",
                start_page=None,
                end_page=None,
            )
            assert resp.success is False
            assert "parse fail" in resp.error
        finally:
            mod.docling_service = original
