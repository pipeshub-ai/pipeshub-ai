"""Unit tests for app.modules.parsers.pptx.pptx_parser.PPTXParser."""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.modules.parsers.pptx.pptx_parser import PPTXParser


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------
class TestPPTXParserInit:
    def test_converter_created(self):
        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter") as mock_cls:
            mock_cls.return_value = MagicMock()
            parser = PPTXParser()
            assert parser.converter is not None
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# parse_binary
# ---------------------------------------------------------------------------
class TestParseBinary:
    def test_success_returns_document(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_document = MagicMock()
        mock_result.document = mock_document
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            with patch("app.modules.parsers.pptx.pptx_parser.DocumentStream") as mock_stream_cls:
                mock_stream = MagicMock()
                mock_stream_cls.return_value = mock_stream

                parser = PPTXParser()
                result = parser.parse_binary(b"fake pptx content")

                assert result is mock_document
                mock_stream_cls.assert_called_once()
                # Verify name is presentation.pptx
                call_kwargs = mock_stream_cls.call_args[1]
                assert call_kwargs["name"] == "presentation.pptx"
                mock_converter.convert.assert_called_once_with(mock_stream)

    def test_creates_bytesio_from_input(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            with patch("app.modules.parsers.pptx.pptx_parser.DocumentStream") as mock_stream_cls:
                mock_stream_cls.return_value = MagicMock()

                parser = PPTXParser()
                parser.parse_binary(b"test data")

                # The stream argument should be a BytesIO object
                call_kwargs = mock_stream_cls.call_args[1]
                stream_arg = call_kwargs["stream"]
                assert isinstance(stream_arg, BytesIO)
                # Read the BytesIO to verify contents
                assert stream_arg.read() == b"test data"

    def test_failure_raises_value_error(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "failure"
        mock_result.status.__str__ = lambda self: "failure"
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            with patch("app.modules.parsers.pptx.pptx_parser.DocumentStream", return_value=MagicMock()):
                parser = PPTXParser()
                with pytest.raises(ValueError, match="Failed to parse PPTX"):
                    parser.parse_binary(b"bad data")

    def test_converter_exception_propagates(self):
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = RuntimeError("Corrupt file")

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            with patch("app.modules.parsers.pptx.pptx_parser.DocumentStream", return_value=MagicMock()):
                parser = PPTXParser()
                with pytest.raises(RuntimeError, match="Corrupt file"):
                    parser.parse_binary(b"data")

    def test_empty_bytes(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            with patch("app.modules.parsers.pptx.pptx_parser.DocumentStream") as mock_stream_cls:
                mock_stream_cls.return_value = MagicMock()

                parser = PPTXParser()
                result = parser.parse_binary(b"")
                assert result is mock_result.document


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------
class TestParseFile:
    def test_success_returns_document(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_document = MagicMock()
        mock_result.document = mock_document
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            parser = PPTXParser()
            result = parser.parse_file("/path/to/presentation.pptx")

            assert result is mock_document
            mock_converter.convert.assert_called_once_with("/path/to/presentation.pptx")

    def test_failure_raises_value_error(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "error"
        mock_result.status.__str__ = lambda self: "error"
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            parser = PPTXParser()
            with pytest.raises(ValueError, match="Failed to parse PPTX"):
                parser.parse_file("/path/to/bad.pptx")

    def test_file_not_found_propagates(self):
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = FileNotFoundError("No such file")

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            parser = PPTXParser()
            with pytest.raises(FileNotFoundError, match="No such file"):
                parser.parse_file("/nonexistent/file.pptx")

    def test_passes_file_path_directly(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            parser = PPTXParser()
            parser.parse_file("/some/path/file.pptx")
            mock_converter.convert.assert_called_once_with("/some/path/file.pptx")

    def test_converter_reused_across_calls(self):
        """The same converter instance is used for multiple parse_file calls."""
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
            parser = PPTXParser()
            parser.parse_file("/path/a.pptx")
            parser.parse_file("/path/b.pptx")
            assert mock_converter.convert.call_count == 2

    def test_different_status_values_fail(self):
        """Any status besides 'success' should raise ValueError."""
        for status_val in ["partial", "error", "timeout", "unknown"]:
            mock_converter = MagicMock()
            mock_result = MagicMock()
            mock_result.status.value = status_val
            mock_converter.convert.return_value = mock_result

            with patch("app.modules.parsers.pptx.pptx_parser.DocumentConverter", return_value=mock_converter):
                parser = PPTXParser()
                with pytest.raises(ValueError):
                    parser.parse_file("/path/to/file.pptx")
