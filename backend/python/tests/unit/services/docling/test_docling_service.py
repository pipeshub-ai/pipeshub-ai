"""Unit tests for app.services.docling.docling_service."""

import asyncio
import base64
import json
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

    # Mock app.modules.parsers.pdf.docling which may have heavy deps
    if "app.modules.parsers.pdf.docling" not in sys.modules:
        sys.modules["app.modules.parsers.pdf.docling"] = MagicMock()

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
    set_docling_service,
    serialize_blocks_container,
    serialize_docling_doc,
    deserialize_docling_doc,
)

# Try to import request/response models (they're Pydantic BaseModel subclasses)
try:
    from app.services.docling.docling_service import (
        CreateBlocksRequest,
        CreateBlocksResponse,
        ParseRequest,
        ParseResponse,
        ProcessRequest,
        ProcessResponse,
    )
    HAS_PYDANTIC_MODELS = True
except Exception:
    HAS_PYDANTIC_MODELS = False


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


class TestDoclingServiceProcessPdf:
    @pytest.mark.asyncio
    async def test_process_pdf_not_initialized(self):
        svc = DoclingService()
        with pytest.raises(RuntimeError, match="not initialized"):
            await svc.process_pdf("test.pdf", b"data")

    @pytest.mark.asyncio
    async def test_process_pdf_success(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_result = MagicMock()
        mock_processor.load_document = AsyncMock(return_value=mock_result)
        svc.processor = mock_processor

        result = await svc.process_pdf("test.pdf", b"data")
        assert result is mock_result
        mock_processor.load_document.assert_awaited_once_with("test.pdf", b"data")

    @pytest.mark.asyncio
    async def test_process_pdf_returns_false(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_processor.load_document = AsyncMock(return_value=False)
        svc.processor = mock_processor

        with pytest.raises(ValueError, match="returned False"):
            await svc.process_pdf("test.pdf", b"data")

    @pytest.mark.asyncio
    async def test_process_pdf_processor_error(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_processor.load_document = AsyncMock(side_effect=RuntimeError("parse fail"))
        svc.processor = mock_processor

        with pytest.raises(RuntimeError, match="parse fail"):
            await svc.process_pdf("test.pdf", b"data")


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

    @pytest.mark.asyncio
    async def test_parse_pdf_error(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_processor.parse_document = AsyncMock(side_effect=ValueError("bad pdf"))
        svc.processor = mock_processor

        with pytest.raises(ValueError, match="bad pdf"):
            await svc.parse_pdf_only("test.pdf", b"data")


class TestDoclingServiceCreateBlocks:
    @pytest.mark.asyncio
    async def test_create_blocks_not_initialized(self):
        svc = DoclingService()
        with pytest.raises(RuntimeError, match="not initialized"):
            await svc.create_blocks_from_parse_result(MagicMock())

    @pytest.mark.asyncio
    async def test_create_blocks_success(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_blocks = MagicMock()
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)
        svc.processor = mock_processor

        result = await svc.create_blocks_from_parse_result(MagicMock())
        assert result is mock_blocks

    @pytest.mark.asyncio
    async def test_create_blocks_with_page_number(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_blocks = MagicMock()
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)
        svc.processor = mock_processor

        doc = MagicMock()
        result = await svc.create_blocks_from_parse_result(doc, page_number=5)
        mock_processor.create_blocks.assert_awaited_once_with(doc, page_number=5)

    @pytest.mark.asyncio
    async def test_create_blocks_returns_false(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_processor.create_blocks = AsyncMock(return_value=False)
        svc.processor = mock_processor

        with pytest.raises(ValueError, match="returned False"):
            await svc.create_blocks_from_parse_result(MagicMock())

    @pytest.mark.asyncio
    async def test_create_blocks_processor_error(self):
        svc = DoclingService()
        mock_processor = MagicMock()
        mock_processor.create_blocks = AsyncMock(side_effect=RuntimeError("block fail"))
        svc.processor = mock_processor

        with pytest.raises(RuntimeError, match="block fail"):
            await svc.create_blocks_from_parse_result(MagicMock())


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

class TestSerializeBlocksContainer:
    def test_success(self):
        container = MagicMock()
        container.dict.return_value = {"blocks": []}
        result = serialize_blocks_container(container)
        assert result == {"blocks": []}

    def test_failure_raises_typeerror(self):
        container = MagicMock()
        container.dict.side_effect = Exception("serialization error")
        with pytest.raises(TypeError, match="Failed to serialize BlocksContainer"):
            serialize_blocks_container(container)


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


class TestDeserializeDoclingDoc:
    @patch("app.services.docling.docling_service.DoclingDocument")
    def test_success(self, mock_doc_cls):
        mock_doc = MagicMock()
        mock_doc_cls.model_validate_json.return_value = mock_doc
        result = deserialize_docling_doc('{"pages": []}')
        assert result is mock_doc

    @patch("app.services.docling.docling_service.DoclingDocument")
    def test_failure_raises_typeerror(self, mock_doc_cls):
        mock_doc_cls.model_validate_json.side_effect = Exception("bad json")
        with pytest.raises(TypeError, match="Failed to deserialize DoclingDocument"):
            deserialize_docling_doc("invalid")


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

@pytest.mark.skipif(not HAS_PYDANTIC_MODELS, reason="Pydantic models not available")
class TestRequestResponseModels:
    def test_process_request(self):
        req = ProcessRequest(record_name="test.pdf", pdf_binary="dGVzdA==")
        assert req.record_name == "test.pdf"
        assert req.org_id is None

    def test_process_request_with_org(self):
        req = ProcessRequest(record_name="test.pdf", pdf_binary="dGVzdA==", org_id="org1")
        assert req.org_id == "org1"

    def test_process_response_success(self):
        resp = ProcessResponse(success=True, block_containers={"blocks": []})
        assert resp.success is True
        assert resp.error is None

    def test_process_response_error(self):
        resp = ProcessResponse(success=False, error="failed")
        assert resp.success is False
        assert resp.error == "failed"

    def test_parse_request(self):
        req = ParseRequest(record_name="doc.pdf", pdf_binary="abc")
        assert req.record_name == "doc.pdf"

    def test_parse_response(self):
        resp = ParseResponse(success=True, parse_result='{"doc": true}')
        assert resp.parse_result == '{"doc": true}'

    def test_create_blocks_request(self):
        req = CreateBlocksRequest(parse_result='{"doc": true}', page_number=3)
        assert req.page_number == 3

    def test_create_blocks_request_no_page(self):
        req = CreateBlocksRequest(parse_result='{"doc": true}')
        assert req.page_number is None

    def test_create_blocks_response(self):
        resp = CreateBlocksResponse(success=True, block_containers={"blocks": []})
        assert resp.success is True


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


class TestProcessPdfEndpoint:
    """Tests for the /process-pdf endpoint."""

    @pytest.mark.asyncio
    async def test_process_pdf_endpoint_invalid_base64(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        mod.docling_service = DoclingService()
        try:
            from app.services.docling.docling_service import process_pdf_endpoint
            req = ProcessRequest(record_name="test.pdf", pdf_binary="not-valid-base64!!!")
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await process_pdf_endpoint(req)
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_process_pdf_endpoint_service_not_available(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        mod.docling_service = None
        try:
            from app.services.docling.docling_service import process_pdf_endpoint
            import base64
            req = ProcessRequest(record_name="test.pdf", pdf_binary=base64.b64encode(b"data").decode())
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await process_pdf_endpoint(req)
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_process_pdf_endpoint_success(self):
        import app.services.docling.docling_service as mod
        import base64
        original = mod.docling_service
        svc = DoclingService()
        mock_result = MagicMock()
        mock_result.dict.return_value = {"blocks": []}
        svc.process_pdf = AsyncMock(return_value=mock_result)
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import process_pdf_endpoint
            req = ProcessRequest(record_name="test.pdf", pdf_binary=base64.b64encode(b"data").decode())
            resp = await process_pdf_endpoint(req)
            assert resp.success is True
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_process_pdf_endpoint_processing_error(self):
        import app.services.docling.docling_service as mod
        import base64
        original = mod.docling_service
        svc = DoclingService()
        svc.process_pdf = AsyncMock(side_effect=ValueError("processing error"))
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import process_pdf_endpoint
            req = ProcessRequest(record_name="test.pdf", pdf_binary=base64.b64encode(b"data").decode())
            resp = await process_pdf_endpoint(req)
            assert resp.success is False
            assert "processing error" in resp.error
        finally:
            mod.docling_service = original


class TestParsePdfEndpoint:
    """Tests for the /parse-pdf endpoint."""

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_invalid_base64(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        mod.docling_service = DoclingService()
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            req = ParseRequest(record_name="test.pdf", pdf_binary="!!invalid!!")
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await parse_pdf_endpoint(req)
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_service_not_available(self):
        import app.services.docling.docling_service as mod
        import base64
        original = mod.docling_service
        mod.docling_service = None
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            req = ParseRequest(record_name="test.pdf", pdf_binary=base64.b64encode(b"data").decode())
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await parse_pdf_endpoint(req)
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_success(self):
        import app.services.docling.docling_service as mod
        import base64
        original = mod.docling_service
        svc = DoclingService()
        mock_doc = MagicMock()
        mock_doc.model_dump_json.return_value = '{"pages": []}'
        svc.parse_pdf_only = AsyncMock(return_value=mock_doc)
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            req = ParseRequest(record_name="test.pdf", pdf_binary=base64.b64encode(b"data").decode())
            resp = await parse_pdf_endpoint(req)
            assert resp.success is True
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_parse_pdf_endpoint_error(self):
        import app.services.docling.docling_service as mod
        import base64
        original = mod.docling_service
        svc = DoclingService()
        svc.parse_pdf_only = AsyncMock(side_effect=RuntimeError("parse fail"))
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import parse_pdf_endpoint
            req = ParseRequest(record_name="test.pdf", pdf_binary=base64.b64encode(b"data").decode())
            resp = await parse_pdf_endpoint(req)
            assert resp.success is False
            assert "parse fail" in resp.error
        finally:
            mod.docling_service = original


class TestCreateBlocksEndpoint:
    """Tests for the /create-blocks endpoint."""

    @pytest.mark.asyncio
    async def test_create_blocks_endpoint_invalid_parse_result(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        svc = DoclingService()
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import create_blocks_endpoint
            # Mock the deserialize to raise
            with patch("app.services.docling.docling_service.deserialize_docling_doc", side_effect=TypeError("bad")):
                req = CreateBlocksRequest(parse_result="invalid")
                from fastapi import HTTPException
                with pytest.raises(HTTPException):
                    await create_blocks_endpoint(req)
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_create_blocks_endpoint_service_not_available(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        mod.docling_service = None
        try:
            from app.services.docling.docling_service import create_blocks_endpoint
            mock_doc = MagicMock()
            with patch("app.services.docling.docling_service.deserialize_docling_doc", return_value=mock_doc):
                req = CreateBlocksRequest(parse_result='{"valid": true}')
                from fastapi import HTTPException
                with pytest.raises(HTTPException):
                    await create_blocks_endpoint(req)
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_create_blocks_endpoint_success(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        svc = DoclingService()
        mock_blocks = MagicMock()
        mock_blocks.dict.return_value = {"blocks": []}
        svc.create_blocks_from_parse_result = AsyncMock(return_value=mock_blocks)
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import create_blocks_endpoint
            mock_doc = MagicMock()
            with patch("app.services.docling.docling_service.deserialize_docling_doc", return_value=mock_doc):
                req = CreateBlocksRequest(parse_result='{"valid": true}')
                resp = await create_blocks_endpoint(req)
                assert resp.success is True
        finally:
            mod.docling_service = original

    @pytest.mark.asyncio
    async def test_create_blocks_endpoint_error(self):
        import app.services.docling.docling_service as mod
        original = mod.docling_service
        svc = DoclingService()
        svc.create_blocks_from_parse_result = AsyncMock(side_effect=RuntimeError("fail"))
        mod.docling_service = svc
        try:
            from app.services.docling.docling_service import create_blocks_endpoint
            mock_doc = MagicMock()
            with patch("app.services.docling.docling_service.deserialize_docling_doc", return_value=mock_doc):
                req = CreateBlocksRequest(parse_result='{"valid": true}')
                resp = await create_blocks_endpoint(req)
                assert resp.success is False
                assert "fail" in resp.error
        finally:
            mod.docling_service = original
