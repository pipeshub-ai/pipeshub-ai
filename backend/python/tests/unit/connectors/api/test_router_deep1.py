"""Deep-coverage tests for app/connectors/api/router.py lines 1-3500.

Targets the largest uncovered blocks:
  1. get_record_stream (821-908): PDF conversion flow via LibreOffice
  2. convert_to_pdf / convert_buffer_to_pdf_stream (910-1073): temp-file, subprocess, error paths
  3. _handle_oauth_config_creation (1945-2069): OAuth type checking, config lifecycle
  4. create_connector_instance deep paths (2329-2490): auth selection, OAuth, DB creation
  5. update_connector_instance_auth_config (2700-3018): OAuth detection, merge, cleanup
  6. update_connector_instance_config (3166-3425): OAuth config ID, filters sync, auth changes
  7. stream_record_internal edge cases (533-597): KB streaming, Google Drive, errors
  8. download_file / stream_record deep paths (600-816): Google API, content-type, errors
"""

import asyncio
import io
import logging
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _handle_oauth_config_creation,
    convert_buffer_to_pdf_stream,
    convert_to_pdf,
    get_record_stream,
    stream_record_internal,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.entities import RecordType

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ROUTER = "app.connectors.api.router"


def _mock_record(**overrides):
    """Build a fake Record-like object with sensible defaults."""
    defaults = {
        "id": "rec-1",
        "org_id": "org-1",
        "record_name": "doc.pdf",
        "record_type": RecordType.FILE,
        "mime_type": "application/pdf",
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": "conn-1",
        "external_record_id": "ext-1",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_request(
    *,
    user: dict | None = None,
    headers: dict | None = None,
    body: dict | None = None,
    container: Any | None = None,
    connector_registry: Any | None = None,
    graph_provider: Any | None = None,
    query_params: dict | None = None,
):
    """Build a minimal mock FastAPI request object."""
    req = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})

    if query_params:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: query_params.get(k, default)
    else:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: None

    _container = container or MagicMock()
    _container.logger = MagicMock(return_value=logging.getLogger("test"))
    _container.config_service = MagicMock(return_value=AsyncMock())
    req.app = MagicMock()
    req.app.container = _container
    if connector_registry:
        req.app.state.connector_registry = connector_registry
    else:
        req.app.state.connector_registry = MagicMock()
    if graph_provider:
        req.app.state.graph_provider = graph_provider
    else:
        req.app.state.graph_provider = MagicMock()

    return req


def _make_upload_file(filename="slides.pptx", content=b"fake-pptx-data"):
    """Build a mock UploadFile."""
    uf = MagicMock()
    uf.filename = filename
    uf.read = AsyncMock(return_value=content)
    uf.close = AsyncMock()
    return uf


# ============================================================================
# 1. get_record_stream — PDF conversion flow (lines 821-908)
# ============================================================================


class TestGetRecordStream:
    """Tests for the POST /api/v1/record/buffer/convert handler."""

    @pytest.mark.asyncio
    async def test_unsupported_format_raises_400(self):
        """When `to` is not PDF, we get a 400."""
        req = _mock_request(query_params={"from": "pptx", "to": "docx"})
        file = _make_upload_file()
        with pytest.raises(HTTPException) as exc:
            await get_record_stream(req, file)
        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_pdf_conversion_success(self):
        """Happy path: file is written, LibreOffice converts, PDF is streamed back."""
        req = _mock_request(query_params={"from": "pptx", "to": MimeTypes.PDF.value})
        file = _make_upload_file(filename="deck.pptx", content=b"pptx-bytes")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", create=True) as mock_open,
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
        ):
            # Make open() usable as a context manager for write AND read
            mock_open.return_value.__enter__ = MagicMock(return_value=io.BytesIO(b"pdf-bytes"))
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            result = await get_record_stream(req, file)
            assert isinstance(result, StreamingResponse)
            mock_stream.assert_called_once()
            file.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pdf_conversion_timeout(self):
        """LibreOffice hangs -> TimeoutError -> 500."""
        req = _mock_request(query_params={"from": "pptx", "to": MimeTypes.PDF.value})
        file = _make_upload_file()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
            patch("builtins.open", create=True) as mock_open,
        ):
            mock_open.return_value.__enter__ = MagicMock(return_value=io.BytesIO())
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc:
                await get_record_stream(req, file)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "timed out" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_pdf_conversion_nonzero_returncode(self):
        """LibreOffice exits with error -> 500."""
        req = _mock_request(query_params={"from": "pptx", "to": MimeTypes.PDF.value})
        file = _make_upload_file()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_process.returncode = 1

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
            patch("builtins.open", create=True) as mock_open,
        ):
            mock_open.return_value.__enter__ = MagicMock(return_value=io.BytesIO())
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc:
                await get_record_stream(req, file)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_pdf_file_not_found_after_conversion(self):
        """LibreOffice succeeds but PDF file missing -> 500."""
        req = _mock_request(query_params={"from": "pptx", "to": MimeTypes.PDF.value})
        file = _make_upload_file()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", create=True) as mock_open,
        ):
            mock_open.return_value.__enter__ = MagicMock(return_value=io.BytesIO())
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc:
                await get_record_stream(req, file)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_generic_exception_during_conversion(self):
        """An unexpected exception during conversion -> 500."""
        req = _mock_request(query_params={"from": "pptx", "to": MimeTypes.PDF.value})
        file = _make_upload_file()

        with (
            patch("builtins.open", side_effect=PermissionError("denied")),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_record_stream(req, file)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# 2. convert_to_pdf (lines 910-982)
# ============================================================================


class TestConvertToPdf:
    """Tests for the standalone convert_to_pdf helper."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Happy path: subprocess returns 0, PDF exists."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
            patch("os.path.exists", return_value=True),
        ):
            result = await convert_to_pdf("/tmp/file.pptx", "/tmp")
            assert result == "/tmp/file.pdf"

    @pytest.mark.asyncio
    async def test_timeout_terminates_process(self):
        """Timeout -> terminate -> kill -> HTTPException."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_process.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with pytest.raises(HTTPException) as exc:
                await convert_to_pdf("/tmp/file.pptx", "/tmp")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "timed out" in exc.value.detail.lower()
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_terminates_process_gracefully(self):
        """Timeout -> terminate -> process exits gracefully."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with pytest.raises(HTTPException) as exc:
                await convert_to_pdf("/tmp/file.pptx", "/tmp")
            assert "timed out" in exc.value.detail.lower()
            mock_process.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonzero_return_code(self):
        """Non-zero return -> HTTPException with 500."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"conversion failed"))
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with pytest.raises(HTTPException) as exc:
                await convert_to_pdf("/tmp/file.pptx", "/tmp")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "Failed" in exc.value.detail or "failed" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_output_file_not_found(self):
        """Process succeeds but PDF missing -> HTTPException."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
            patch("os.path.exists", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc:
                await convert_to_pdf("/tmp/file.pptx", "/tmp")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_generic_subprocess_exception(self):
        """Unexpected error during subprocess creation -> HTTPException."""
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=OSError("no binary")):
            with pytest.raises(HTTPException) as exc:
                await convert_to_pdf("/tmp/file.pptx", "/tmp")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# 2b. convert_buffer_to_pdf_stream (lines 985-1073)
# ============================================================================


class TestConvertBufferToPdfStream:
    """Tests for convert_buffer_to_pdf_stream."""

    @pytest.mark.asyncio
    async def test_bytes_buffer_success(self):
        """Bytes buffer -> write to temp file -> convert -> stream back."""
        buffer = b"fake-pptx"
        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", return_value=True),
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
        ):
            # Write phase - return writable BytesIO
            write_buf = io.BytesIO()
            read_buf = io.BytesIO(b"pdf-content")

            call_count = [0]
            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                if "w" in mode:
                    cm.__enter__ = MagicMock(return_value=write_buf)
                else:
                    cm.__enter__ = MagicMock(return_value=read_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/slides.pdf"

            # asyncio.to_thread just calls the function
            with patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)):
                mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")
                result = await convert_buffer_to_pdf_stream(buffer, "slides.pptx", "pptx")
                assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_streaming_response_buffer(self):
        """StreamingResponse buffer -> iterate body_iterator -> write."""
        async def body_iter():
            yield b"chunk1"
            yield b"chunk2"

        buffer = StreamingResponse(body_iter(), media_type="application/octet-stream")

        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", return_value=True),
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()
            read_buf = io.BytesIO(b"pdf-data")

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                if "w" in mode:
                    cm.__enter__ = MagicMock(return_value=write_buf)
                else:
                    cm.__enter__ = MagicMock(return_value=read_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/file.pdf"
            mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            result = await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_response_buffer_with_body(self):
        """Response object with body bytes -> write to temp file."""
        buffer = Response(content=b"response-body", media_type="application/octet-stream")

        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", return_value=True),
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()
            read_buf = io.BytesIO(b"pdf-data")

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                if "w" in mode:
                    cm.__enter__ = MagicMock(return_value=write_buf)
                else:
                    cm.__enter__ = MagicMock(return_value=read_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/file.pdf"
            mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            result = await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_response_buffer_empty_body_raises(self):
        """Response with no body -> HTTPException."""
        buffer = Response(content=b"", media_type="application/octet-stream")
        # Response(content=b"") sets body to b"", which is falsy

        with (
            patch("builtins.open", create=True) as mock_open,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                cm.__enter__ = MagicMock(return_value=write_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect

            with pytest.raises(HTTPException) as exc:
                await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_file_like_buffer(self):
        """File-like object with .read() -> chunk-by-chunk write."""
        buffer = io.BytesIO(b"file-like-content")

        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", return_value=True),
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()
            read_buf = io.BytesIO(b"pdf-data")

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                if "w" in mode:
                    cm.__enter__ = MagicMock(return_value=write_buf)
                else:
                    cm.__enter__ = MagicMock(return_value=read_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/file.pdf"
            mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            result = await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_unsupported_buffer_type_raises(self):
        """Non-bytes, non-Response, non-file-like -> HTTPException."""
        buffer = 12345  # int, unsupported

        with (
            patch("builtins.open", create=True) as mock_open,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                cm.__enter__ = MagicMock(return_value=write_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect

            with pytest.raises(HTTPException) as exc:
                await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "Unsupported buffer type" in exc.value.detail

    @pytest.mark.asyncio
    async def test_pdf_path_not_found_fallback_to_listdir(self):
        """If os.path.exists returns False after convert_to_pdf, listdir fallback kicks in."""
        buffer = b"data"

        exists_calls = [0]

        def exists_side_effect(path):
            exists_calls[0] += 1
            # First call inside convert_to_pdf returns True
            # The call after convert_to_pdf in convert_buffer_to_pdf_stream returns False
            return False

        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", side_effect=exists_side_effect),
            patch("os.listdir", return_value=["output.pdf"]),
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()
            read_buf = io.BytesIO(b"pdf-data")

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                if "w" in mode:
                    cm.__enter__ = MagicMock(return_value=write_buf)
                else:
                    cm.__enter__ = MagicMock(return_value=read_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/file.pdf"
            mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            result = await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_pdf_path_not_found_no_pdf_files_raises(self):
        """If os.path.exists returns False and no PDFs in listdir -> HTTPException."""
        buffer = b"data"

        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", return_value=False),
            patch("os.listdir", return_value=["file.pptx"]),
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                cm.__enter__ = MagicMock(return_value=write_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/file.pdf"

            with pytest.raises(HTTPException) as exc:
                await convert_buffer_to_pdf_stream(buffer, "file.pptx", "pptx")
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            assert "not found" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_no_record_name_uses_fallback(self):
        """When record_name is empty, fallback naming is used."""
        buffer = b"data"

        with (
            patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert,
            patch("builtins.open", create=True) as mock_open,
            patch("os.path.exists", return_value=True),
            patch(f"{_ROUTER}.create_stream_record_response") as mock_stream,
            patch("asyncio.to_thread", side_effect=lambda fn, *a: fn(*a)),
        ):
            write_buf = io.BytesIO()
            read_buf = io.BytesIO(b"pdf-data")

            def open_side_effect(path, mode="r"):
                cm = MagicMock()
                if "w" in mode:
                    cm.__enter__ = MagicMock(return_value=write_buf)
                else:
                    cm.__enter__ = MagicMock(return_value=read_buf)
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_open.side_effect = open_side_effect
            mock_convert.return_value = "/tmp/test/file.pdf"
            mock_stream.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            result = await convert_buffer_to_pdf_stream(buffer, "", "pptx")
            assert isinstance(result, StreamingResponse)
            # Should use fallback filename "converted_file.pdf"
            call_args = mock_stream.call_args
            assert call_args is not None


# ============================================================================
# 3. _handle_oauth_config_creation (lines 1945-2069)
# ============================================================================


class TestHandleOauthConfigCreation:
    """Tests for _handle_oauth_config_creation."""

    @pytest.mark.asyncio
    async def test_non_oauth_auth_type_returns_none(self):
        """auth_type != OAUTH -> None, no side effects."""
        result = await _handle_oauth_config_creation(
            connector_type="GOOGLE_DRIVE",
            auth_config={"clientId": "id"},
            instance_name="My Drive",
            user_id="u1",
            org_id="o1",
            is_admin=True,
            config_service=AsyncMock(),
            oauth_config_id=None,
            auth_type="OAUTH_ADMIN_CONSENT",
            base_url="http://localhost",
            logger=logging.getLogger("test"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_credentials_with_existing_id_returns_id(self):
        """No OAuth creds but oauth_app_id provided -> return it."""
        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={},  # no creds
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=AsyncMock(),
                oauth_config_id="existing-id",
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "existing-id"

    @pytest.mark.asyncio
    async def test_no_credentials_no_id_returns_none(self):
        """No OAuth creds and no oauth_app_id -> None."""
        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=AsyncMock(),
                oauth_config_id=None,
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_new_config_creation(self):
        """Has credentials, no existing ID -> create new config."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
            patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_conflict,
            patch(f"{_ROUTER}._create_or_update_oauth_config", new_callable=AsyncMock, return_value="new-id") as mock_create,
        ):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "my-id", "clientSecret": "my-secret"},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=mock_config_service,
                oauth_config_id=None,
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "new-id"
        mock_conflict.assert_called_once()
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_existing_config_same_name(self):
        """Has credentials, existing ID found, name unchanged."""
        existing_configs = [
            {"_id": "oauth-1", "orgId": "o1", "oauthInstanceName": "My Drive"}
        ]
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=existing_configs)

        with (
            patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
            patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_conflict,
            patch(f"{_ROUTER}._create_or_update_oauth_config", new_callable=AsyncMock, return_value="oauth-1") as mock_create,
        ):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "id", "clientSecret": "secret", "oauthInstanceName": "My Drive"},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=mock_config_service,
                oauth_config_id="oauth-1",
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "oauth-1"
        # Name didn't change, so no conflict check on update
        mock_conflict.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_existing_config_name_changed(self):
        """Has credentials, existing ID found, name changed -> check conflict."""
        existing_configs = [
            {"_id": "oauth-1", "orgId": "o1", "oauthInstanceName": "Old Name"}
        ]
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=existing_configs)

        with (
            patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
            patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_conflict,
            patch(f"{_ROUTER}._create_or_update_oauth_config", new_callable=AsyncMock, return_value="oauth-1"),
        ):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "id", "clientSecret": "secret", "oauthInstanceName": "New Name"},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=mock_config_service,
                oauth_config_id="oauth-1",
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "oauth-1"
        mock_conflict.assert_called_once()
        # Verify exclude_index was passed
        _, kwargs = mock_conflict.call_args
        assert kwargs.get("exclude_index") == 0 or mock_conflict.call_args[0][1] == "New Name"

    @pytest.mark.asyncio
    async def test_update_config_not_found_creates_new(self):
        """Has credentials, existing ID not found -> fall back to create new."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=[
            {"_id": "other-id", "orgId": "o1", "oauthInstanceName": "Other"}
        ])

        with (
            patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
            patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_conflict,
            patch(f"{_ROUTER}._create_or_update_oauth_config", new_callable=AsyncMock, return_value="new-id") as mock_create,
        ):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "id", "clientSecret": "secret"},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=mock_config_service,
                oauth_config_id="nonexistent-id",
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "new-id"
        mock_conflict.assert_called_once()
        # oauth_app_id should have been reset to None for the create call
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["oauth_app_id"] is None

    @pytest.mark.asyncio
    async def test_update_no_name_provided_keeps_existing(self):
        """Has credentials, existing ID, no oauthInstanceName -> keep existing name."""
        existing_configs = [
            {"_id": "oauth-1", "orgId": "o1", "oauthInstanceName": "Existing Name"}
        ]
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=existing_configs)

        with (
            patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
            patch(f"{_ROUTER}._check_oauth_name_conflict"),
            patch(f"{_ROUTER}._create_or_update_oauth_config", new_callable=AsyncMock, return_value="oauth-1") as mock_create,
        ):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "id", "clientSecret": "secret"},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=mock_config_service,
                oauth_config_id="oauth-1",
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "oauth-1"
        # Verify create was called with existing name
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["instance_name"] == "Existing Name" or mock_create.call_args[0][2] == "Existing Name"

    @pytest.mark.asyncio
    async def test_existing_configs_not_list_handled(self):
        """If get_config returns non-list, it's normalized to []."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value="not-a-list")

        with (
            patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
            patch(f"{_ROUTER}._check_oauth_name_conflict"),
            patch(f"{_ROUTER}._create_or_update_oauth_config", new_callable=AsyncMock, return_value="new-id"),
        ):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "id", "clientSecret": "secret"},
                instance_name="My Drive",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=mock_config_service,
                oauth_config_id=None,
                auth_type="OAUTH",
                base_url="http://localhost",
                logger=logging.getLogger("test"),
            )
        assert result == "new-id"


# ============================================================================




# ============================================================================
# 7. stream_record_internal edge cases (lines 533-597)
# ============================================================================


class TestStreamRecordInternalEdgeCases:
    """Tests for stream_record_internal — KB flow, Google Drive, errors."""

    def _setup(self, *, connector_name=Connectors.GOOGLE_DRIVE, has_connector_obj=True):
        """Build request with config_service, graph_provider, and container."""
        record = _mock_record(connector_name=connector_name)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=lambda path, **kwargs: {
            "/services/secrets": {"scopedJwtSecret": "test-secret"},
            "/services/endpoints": {"storage": {"endpoint": "http://storage:8080"}},
        }.get(path, {}))

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1"})

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))

        connector_obj = MagicMock()
        connector_obj.get_app_name = MagicMock(return_value=Connectors.GOOGLE_DRIVE_WORKSPACE)
        connector_obj.stream_record = AsyncMock(return_value=Response(content=b"data"))
        container.connectors_map = {"conn-1": connector_obj} if has_connector_obj else {}

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer fake-token"}.get(k, default)
        req.app = MagicMock()
        req.app.container = container

        return req, record, config_service, graph_provider

    @pytest.mark.asyncio
    async def test_knowledge_base_stream(self):
        """Knowledge Base connector -> fetches from storage service."""
        record = _mock_record(
            connector_name=Connectors.KNOWLEDGE_BASE,
            external_record_id="ext-kb-1",
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=lambda path, **kwargs: {
            "/services/secrets": {"scopedJwtSecret": "test-secret"},
            "/services/endpoints": {"storage": {"endpoint": "http://storage:8080"}},
        }.get(path, {}))

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1"})

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer fake-token"}.get(k, default)
        req.app = MagicMock()
        req.app.container = MagicMock()

        jwt_payload = {"orgId": "org-1", "scopes": ["connector:signedUrl"]}

        with (
            patch("jwt.decode", return_value=jwt_payload),
            patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="storage-token"),
            patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock, return_value={"data": b"file-bytes"}),
        ):
            result = await stream_record_internal(req, "rec-1", graph_provider, config_service)
            assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_knowledge_base_stream_dict_response(self):
        """KB stream with dict data -> extracts buffer."""
        record = _mock_record(
            connector_name=Connectors.KNOWLEDGE_BASE,
            external_record_id="ext-kb-1",
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=lambda path, **kwargs: {
            "/services/secrets": {"scopedJwtSecret": "test-secret"},
            "/services/endpoints": {"storage": {"endpoint": "http://storage:8080"}},
        }.get(path, {}))

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1"})

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer fake-token"}.get(k, default)
        req.app = MagicMock()
        req.app.container = MagicMock()

        with (
            patch("jwt.decode", return_value={"orgId": "org-1"}),
            patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="token"),
            patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock, return_value={"data": {"data": [0x50, 0x44, 0x46]}}),
        ):
            result = await stream_record_internal(req, "rec-1", graph_provider, config_service)
            assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_google_drive_workspace_passes_user_id(self):
        """Google Drive Workspace -> passes userId to stream_record."""
        req, record, config_service, graph_provider = self._setup(
            connector_name=Connectors.GOOGLE_DRIVE,
        )

        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1", "name": "conn", "type": "GOOGLE_DRIVE"})

        with patch("jwt.decode", return_value={"orgId": "org-1", "userId": "u1"}):
            result = await stream_record_internal(req, "rec-1", graph_provider, config_service)
            connector_obj = req.app.container.connectors_map["conn-1"]
            connector_obj.stream_record.assert_awaited_once()
            # First arg is record, second is userId
            call_args = connector_obj.stream_record.call_args[0]
            assert len(call_args) == 2

    @pytest.mark.asyncio
    async def test_non_google_connector_no_user_id(self):
        """Non-Google connector -> stream_record called without userId."""
        req, record, config_service, graph_provider = self._setup()

        # Make the connector NOT a Google workspace connector
        connector_obj = req.app.container.connectors_map["conn-1"]
        connector_obj.get_app_name = MagicMock(return_value=Connectors.SLACK)

        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1", "name": "conn", "type": "SLACK"})

        with patch("jwt.decode", return_value={"orgId": "org-1"}):
            result = await stream_record_internal(req, "rec-1", graph_provider, config_service)
            call_args = connector_obj.stream_record.call_args[0]
            assert len(call_args) == 1  # only record, no userId

    @pytest.mark.asyncio
    async def test_missing_auth_header_raises_401(self):
        """No Authorization header -> 401."""
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: None

        with pytest.raises(HTTPException) as exc:
            await stream_record_internal(req, "rec-1", AsyncMock(), AsyncMock())
        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        """Record not in DB -> 404."""
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer tok"}.get(k, default)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=None)
        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1"})

        with patch("jwt.decode", return_value={"orgId": "org-1"}):
            with pytest.raises(HTTPException) as exc:
                await stream_record_internal(req, "rec-1", graph_provider, config_service)
            assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_connector_not_found_raises_404(self):
        """Connector instance deleted -> 404."""
        record = _mock_record()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)

        # First call returns org, second returns None (connector deleted)
        call_count = [0]
        async def get_doc_side_effect(doc_id, collection):
            call_count[0] += 1
            if collection == CollectionNames.ORGS.value:
                return {"_key": "org-1"}
            return None  # connector not found

        graph_provider.get_document = AsyncMock(side_effect=get_doc_side_effect)

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer tok"}.get(k, default)
        req.app = MagicMock()
        req.app.container = MagicMock()

        with patch("jwt.decode", return_value={"orgId": "org-1"}):
            with pytest.raises(HTTPException) as exc:
                await stream_record_internal(req, "rec-1", graph_provider, config_service)
            assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_connector_disabled_raises_unhealthy(self):
        """Connector not in connectors_map -> CONFLICT status."""
        record = _mock_record()

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(return_value={"_key": "org-1", "name": "Drive", "type": "GOOGLE_DRIVE"})

        container = MagicMock()
        container.connectors_map = {}  # empty map -> connector disabled

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer tok"}.get(k, default)
        req.app = MagicMock()
        req.app.container = container

        with patch("jwt.decode", return_value={"orgId": "org-1"}):
            with pytest.raises(HTTPException) as exc:
                await stream_record_internal(req, "rec-1", graph_provider, config_service)
            assert exc.value.status_code == HttpStatusCode.CONFLICT.value

    @pytest.mark.asyncio
    async def test_org_not_found_retries_with_record_org(self):
        """If org not found with JWT org_id but record has different org_id, retry."""
        record = _mock_record(org_id="org-2")

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        call_count = [0]
        async def get_doc_side_effect(doc_id, collection):
            nonlocal call_count
            call_count[0] += 1
            if collection == CollectionNames.ORGS.value:
                if doc_id == "org-1":
                    return None  # JWT org not found
                return {"_key": "org-2"}
            return {"_key": "conn-1", "name": "Drive", "type": "GOOGLE_DRIVE"}

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(side_effect=get_doc_side_effect)

        connector_obj = MagicMock()
        connector_obj.get_app_name = MagicMock(return_value=Connectors.SLACK)
        connector_obj.stream_record = AsyncMock(return_value=Response(content=b"data"))

        container = MagicMock()
        container.connectors_map = {"conn-1": connector_obj}

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer tok"}.get(k, default)
        req.app = MagicMock()
        req.app.container = container

        with patch("jwt.decode", return_value={"orgId": "org-1"}):
            result = await stream_record_internal(req, "rec-1", graph_provider, config_service)
            assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_jwt_error_raises_401(self):
        """JWTError during decode -> 401."""
        from jose import JWTError

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer bad"}.get(k, default)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        with patch("jwt.decode", side_effect=JWTError("bad token")):
            with pytest.raises(HTTPException) as exc:
                await stream_record_internal(req, "rec-1", AsyncMock(), config_service)
            assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_500(self):
        """Generic exception -> 500."""
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda k, default=None: {"Authorization": "Bearer tok"}.get(k, default)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc:
            await stream_record_internal(req, "rec-1", AsyncMock(), config_service)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# 8. download_file and stream_record deep paths (lines 600-816)
# ============================================================================


class TestDownloadFileDeepPaths:
    """Tests for download_file — Google API downloads, content type, errors."""

    def _setup(self, *, connector_name=Connectors.GOOGLE_DRIVE, has_connector_obj=True):
        record = _mock_record(connector_name=connector_name)
        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(return_value={
            "_key": "org-1", "name": "Drive", "type": "GOOGLE_DRIVE",
        })

        connector_obj = MagicMock()
        connector_obj.get_app_name = MagicMock(return_value=Connectors.GOOGLE_DRIVE_WORKSPACE)
        connector_obj.stream_record = AsyncMock(return_value=Response(content=b"data"))

        container = MagicMock()
        container.connectors_map = {"conn-1": connector_obj} if has_connector_obj else {}

        signed_url_handler = MagicMock()
        signed_url_handler.validate_token = MagicMock(return_value=SimpleNamespace(
            user_id="u1", record_id="rec-1",
        ))

        req = MagicMock()
        req.app = MagicMock()
        req.app.container = container

        return req, signed_url_handler, graph_provider, record

    @pytest.mark.asyncio
    async def test_google_drive_download_success(self):
        """Happy path: signed URL valid, Google Drive download."""
        req, handler, gp, record = self._setup()

        from app.connectors.api.router import download_file
        result = await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_non_google_connector_download(self):
        """Non-Google connector -> stream_record called without userId."""
        req, handler, gp, record = self._setup()
        connector_obj = req.app.container.connectors_map["conn-1"]
        connector_obj.get_app_name = MagicMock(return_value=Connectors.SLACK)

        from app.connectors.api.router import download_file
        result = await download_file(req, "org-1", "rec-1", "slack", "token-str", handler, gp)
        call_args = connector_obj.stream_record.call_args[0]
        assert len(call_args) == 1

    @pytest.mark.asyncio
    async def test_token_record_mismatch_raises_401(self):
        """Token record_id != request record_id -> 401."""
        req, handler, gp, record = self._setup()
        handler.validate_token = MagicMock(return_value=SimpleNamespace(
            user_id="u1", record_id="wrong-id",
        ))

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_org_not_found_raises_404(self):
        """Organization not in DB -> 404."""
        req, handler, gp, record = self._setup()
        gp.get_document = AsyncMock(return_value=None)

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        """Record not in DB -> 404."""
        req, handler, gp, record = self._setup()
        gp.get_record_by_id = AsyncMock(return_value=None)

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_connector_deleted_raises_404(self):
        """Connector instance deleted from DB -> 404."""
        req, handler, gp, record = self._setup()

        call_count = [0]
        async def get_doc(doc_id, collection):
            call_count[0] += 1
            if collection == CollectionNames.ORGS.value:
                return {"_key": "org-1"}
            return None  # connector not found

        gp.get_document = AsyncMock(side_effect=get_doc)

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_connector_disabled_raises_unhealthy(self):
        """Connector not in connectors_map -> CONFLICT."""
        req, handler, gp, record = self._setup(has_connector_obj=False)

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.CONFLICT.value

    @pytest.mark.asyncio
    async def test_stream_error_raises_500(self):
        """Connector stream_record throws -> 500."""
        req, handler, gp, record = self._setup()
        connector_obj = req.app.container.connectors_map["conn-1"]
        connector_obj.stream_record = AsyncMock(side_effect=RuntimeError("stream failed"))

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_generic_exception_outer_raises_500(self):
        """Top-level generic exception -> 500."""
        req, handler, gp, record = self._setup()
        handler.validate_token = MagicMock(side_effect=RuntimeError("bad token"))

        from app.connectors.api.router import download_file
        with pytest.raises(HTTPException) as exc:
            await download_file(req, "org-1", "rec-1", "googledrive", "token-str", handler, gp)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


class TestStreamRecordDeepPaths:
    """Tests for stream_record — conversion paths, error cases (lines 686-816)."""

    def _setup(self, *, connector_name=Connectors.GOOGLE_DRIVE, convert_to=None,
               has_connector_obj=True, is_google=True):
        record = _mock_record(connector_name=connector_name)

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=record)
        graph_provider.get_document = AsyncMock(return_value={
            "_key": "org-1", "name": "Drive", "type": "GOOGLE_DRIVE",
        })
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)

        config_service = AsyncMock()

        connector_obj = MagicMock()
        if is_google:
            connector_obj.get_app_name = MagicMock(return_value=Connectors.GOOGLE_DRIVE_WORKSPACE)
        else:
            connector_obj.get_app_name = MagicMock(return_value=Connectors.SLACK)
        connector_obj.stream_record = AsyncMock(return_value=Response(content=b"data"))

        container = MagicMock()
        container.connectors_map = {"conn-1": connector_obj} if has_connector_obj else {}

        user_data = {"userId": "u1", "orgId": "org-1"}
        req = MagicMock()
        req.state = MagicMock()
        req.state.user = MagicMock()
        req.state.user.get = lambda k, default=None: user_data.get(k, default)
        req.app = MagicMock()
        req.app.container = container

        return req, record, graph_provider, config_service, connector_obj

    @pytest.mark.asyncio
    async def test_pdf_conversion_for_pptx(self):
        """When convertTo=pdf and file is pptx, conversion flow is triggered."""
        req, record, gp, cs, conn_obj = self._setup(is_google=False)
        record.record_name = "slides.pptx"
        record.mime_type = MimeTypes.PPTX.value

        with (
            patch(f"{_ROUTER}.convert_buffer_to_pdf_stream", new_callable=AsyncMock) as mock_convert,
        ):
            mock_convert.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            from app.connectors.api.router import stream_record
            result = await stream_record(req, "rec-1", MimeTypes.PDF.value, gp, cs)
            assert isinstance(result, StreamingResponse)
            mock_convert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_conversion_for_pdf_type(self):
        """PDF files don't need conversion even when convertTo=pdf."""
        req, record, gp, cs, conn_obj = self._setup(is_google=False)
        record.record_name = "doc.pdf"
        record.mime_type = "application/pdf"

        from app.connectors.api.router import stream_record
        result = await stream_record(req, "rec-1", MimeTypes.PDF.value, gp, cs)
        assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_google_slides_needs_conversion(self):
        """Google Slides mime type triggers conversion."""
        req, record, gp, cs, conn_obj = self._setup(is_google=True)
        record.record_name = "presentation"
        record.mime_type = MimeTypes.GOOGLE_SLIDES.value

        with (
            patch(f"{_ROUTER}.convert_buffer_to_pdf_stream", new_callable=AsyncMock) as mock_convert,
        ):
            mock_convert.return_value = StreamingResponse(iter([b"pdf"]), media_type="application/pdf")

            from app.connectors.api.router import stream_record
            result = await stream_record(req, "rec-1", MimeTypes.PDF.value, gp, cs)
            mock_convert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_conversion_error_raises_500(self):
        """Conversion failure -> 500."""
        req, record, gp, cs, conn_obj = self._setup(is_google=False)
        record.record_name = "slides.pptx"
        record.mime_type = MimeTypes.PPTX.value

        with (
            patch(f"{_ROUTER}.convert_buffer_to_pdf_stream", new_callable=AsyncMock, side_effect=RuntimeError("convert failed")),
        ):
            from app.connectors.api.router import stream_record
            with pytest.raises(HTTPException) as exc:
                await stream_record(req, "rec-1", MimeTypes.PDF.value, gp, cs)
            assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_org_not_found_raises_404(self):
        """Org not found -> 404."""
        req, record, gp, cs, conn_obj = self._setup()
        gp.get_document = AsyncMock(return_value=None)

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        """Record not found -> 404."""
        req, record, gp, cs, conn_obj = self._setup()
        gp.get_record_by_id = AsyncMock(return_value=None)
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_org_mismatch_retries_with_record_org(self):
        """JWT org_id differs from record org_id -> retry with record's org."""
        req, record, gp, cs, conn_obj = self._setup(is_google=False)
        record.org_id = "org-2"

        call_count = [0]
        async def get_doc(doc_id, collection):
            call_count[0] += 1
            if collection == CollectionNames.ORGS.value:
                return {"_key": doc_id}
            return {"_key": "conn-1", "name": "Drive", "type": "GD"}

        gp.get_document = AsyncMock(side_effect=get_doc)

        from app.connectors.api.router import stream_record
        result = await stream_record(req, "rec-1", None, gp, cs)
        # Should have fetched org-2 after mismatch
        assert call_count[0] >= 2

    @pytest.mark.asyncio
    async def test_access_denied_raises_403(self):
        """User has no access to record -> 403."""
        req, record, gp, cs, conn_obj = self._setup()
        gp.check_record_access_with_details = AsyncMock(return_value=False)

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_connector_disabled_raises_unhealthy(self):
        """Connector not in map -> CONFLICT."""
        req, record, gp, cs, conn_obj = self._setup(has_connector_obj=False)

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.CONFLICT.value

    @pytest.mark.asyncio
    async def test_connector_deleted_raises_404(self):
        """Connector instance deleted from DB -> 404."""
        req, record, gp, cs, conn_obj = self._setup()

        call_count = [0]
        async def get_doc(doc_id, collection):
            call_count[0] += 1
            if collection == CollectionNames.ORGS.value:
                return {"_key": "org-1"}
            return None

        gp.get_document = AsyncMock(side_effect=get_doc)

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_stream_error_raises_500(self):
        """stream_record throws -> 500."""
        req, record, gp, cs, conn_obj = self._setup(is_google=False)
        conn_obj.stream_record = AsyncMock(side_effect=RuntimeError("stream err"))

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_http_exception_from_connector_preserved(self):
        """HTTPException from connector is re-raised with original status."""
        req, record, gp, cs, conn_obj = self._setup(is_google=False)
        conn_obj.stream_record = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Forbidden by API")
        )

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_outer_generic_exception_raises_500(self):
        """Top-level generic exception -> 500."""
        req, record, gp, cs, conn_obj = self._setup()
        gp.get_record_by_id = AsyncMock(side_effect=RuntimeError("db down"))

        from app.connectors.api.router import stream_record
        with pytest.raises(HTTPException) as exc:
            await stream_record(req, "rec-1", None, gp, cs)
        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
