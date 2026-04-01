"""Unit tests for app.modules.parsers.docx.docx_parser.DocxParser."""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.modules.parsers.docx.docx_parser import DocxParser


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------
class TestDocxParserInit:
    def test_initial_state(self):
        with patch("app.modules.parsers.docx.docx_parser.DocumentConverter"):
            parser = DocxParser()
            assert parser.text_content is None
            assert parser.metadata is None


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------
class TestParse:
    def test_parse_returns_document(self):
        mock_converter_cls = MagicMock()
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_document = MagicMock()
        mock_result.document = mock_document
        mock_converter.convert.return_value = mock_result
        mock_converter_cls.return_value = mock_converter

        with patch("app.modules.parsers.docx.docx_parser.DocumentConverter", mock_converter_cls):
            with patch("app.modules.parsers.docx.docx_parser.DocumentStream") as mock_stream_cls:
                mock_stream = MagicMock()
                mock_stream_cls.return_value = mock_stream

                parser = DocxParser()
                file_binary = BytesIO(b"fake docx content")
                result = parser.parse(file_binary)

                assert result is mock_document
                mock_stream_cls.assert_called_once_with(name="content.docx", stream=file_binary)
                mock_converter.convert.assert_called_once_with(mock_stream)

    def test_parse_creates_document_stream_with_correct_name(self):
        mock_converter_cls = MagicMock()
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_converter.convert.return_value = mock_result
        mock_converter_cls.return_value = mock_converter

        with patch("app.modules.parsers.docx.docx_parser.DocumentConverter", mock_converter_cls):
            with patch("app.modules.parsers.docx.docx_parser.DocumentStream") as mock_stream_cls:
                mock_stream_cls.return_value = MagicMock()

                parser = DocxParser()
                parser.parse(BytesIO(b"data"))

                call_kwargs = mock_stream_cls.call_args
                assert call_kwargs[1]["name"] == "content.docx" or call_kwargs[0][0] == "content.docx"

    def test_parse_with_empty_binary(self):
        mock_converter_cls = MagicMock()
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.document = MagicMock()
        mock_converter.convert.return_value = mock_result
        mock_converter_cls.return_value = mock_converter

        with patch("app.modules.parsers.docx.docx_parser.DocumentConverter", mock_converter_cls):
            with patch("app.modules.parsers.docx.docx_parser.DocumentStream") as mock_stream_cls:
                mock_stream_cls.return_value = MagicMock()

                parser = DocxParser()
                result = parser.parse(BytesIO(b""))
                assert result is mock_result.document

    def test_parse_converter_error_propagates(self):
        mock_converter_cls = MagicMock()
        mock_converter = MagicMock()
        mock_converter.convert.side_effect = RuntimeError("Corrupt docx")
        mock_converter_cls.return_value = mock_converter

        with patch("app.modules.parsers.docx.docx_parser.DocumentConverter", mock_converter_cls):
            with patch("app.modules.parsers.docx.docx_parser.DocumentStream"):
                parser = DocxParser()
                with pytest.raises(RuntimeError, match="Corrupt docx"):
                    parser.parse(BytesIO(b"bad data"))

    def test_parse_uses_new_converter_each_call(self):
        """DocxParser creates a new DocumentConverter inside parse(), not in __init__."""
        call_count = 0

        def counting_converter(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            mock.convert.return_value = MagicMock()
            return mock

        with patch("app.modules.parsers.docx.docx_parser.DocumentConverter", side_effect=counting_converter):
            with patch("app.modules.parsers.docx.docx_parser.DocumentStream", return_value=MagicMock()):
                parser = DocxParser()
                parser.parse(BytesIO(b"data1"))
                parser.parse(BytesIO(b"data2"))
                # __init__ does not create a converter, but each parse() does
                # Actually looking at the code: __init__ has no converter,
                # parse() creates DocumentConverter() each time
                assert call_count == 2


# ---------------------------------------------------------------------------
# main() function and __name__ == "__main__" block
# ---------------------------------------------------------------------------
class TestMain:
    def test_main_raises_because_init_takes_no_file_path(self):
        """main() passes file_path to DocxParser() but __init__ only takes self.
        This covers line 23 (file_path assignment) and line 26 (DocxParser(file_path) call)."""
        from app.modules.parsers.docx.docx_parser import main
        with pytest.raises(TypeError):
            main()

    def test_main_full_path_with_mocked_parser(self):
        """Mock DocxParser so main() reaches parser.parse() on line 29."""
        mock_parser_instance = MagicMock()
        mock_parser_cls = MagicMock(return_value=mock_parser_instance)

        with patch("app.modules.parsers.docx.docx_parser.DocxParser", mock_parser_cls):
            from app.modules.parsers.docx.docx_parser import main
            main()
            # DocxParser was called with the file_path
            mock_parser_cls.assert_called_once()
            # parser.parse() was called
            mock_parser_instance.parse.assert_called_once()