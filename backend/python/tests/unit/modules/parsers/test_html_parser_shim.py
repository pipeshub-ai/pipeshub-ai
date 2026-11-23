"""Full coverage tests for html_parser shim module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.parsers.html_parser import html_parser
from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from tests.support.fresh_module import fresh_copy


class TestHTMLParserProtocol:
    def test_protocol_clean_html_body(self):
        from app.modules.parsers.html_parser.html_parser import HTMLParserProtocol

        class _Concrete(HTMLParserProtocol):
            pass

        assert _Concrete().clean_html("") is None

    def test_protocol_replace_relative_image_urls_body(self):
        from app.modules.parsers.html_parser.html_parser import HTMLParserProtocol

        class _Concrete(HTMLParserProtocol):
            pass

        assert _Concrete().replace_relative_image_urls("") is None

    def test_protocol_extract_and_replace_images_body(self):
        from app.modules.parsers.html_parser.html_parser import HTMLParserProtocol

        class _Concrete(HTMLParserProtocol):
            pass

        assert _Concrete().extract_and_replace_images("") is None

    @pytest.mark.asyncio
    async def test_protocol_parse_to_blocks_body(self):
        from app.modules.parsers.html_parser.html_parser import HTMLParserProtocol

        class _Concrete(HTMLParserProtocol):
            pass

        result = await _Concrete().parse_to_blocks("")
        assert result is None


class TestHTMLParserBackendSelection:
    def test_html_parser_defaults_to_selectolax_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            shim = fresh_copy(html_parser)

        assert shim.HTMLParser is SelectolaxHtmlParser
        assert "HTMLParser" in shim.__all__
        assert "HTMLParserProtocol" in shim.__all__

    def test_html_parser_defaults_to_selectolax(self):
        with patch.dict("os.environ", {"PARSER_BACKEND": "selectolax"}, clear=False):
            shim = fresh_copy(html_parser)

        assert shim.HTMLParser is SelectolaxHtmlParser

    def test_html_parser_can_select_docling_backend(self):
        shared = html_parser.HTMLParser
        with patch.dict("os.environ", {"PARSER_BACKEND": "docling"}, clear=False):
            with patch(
                "docling.document_converter.DocumentConverter"
            ):
                shim = fresh_copy(html_parser)

        assert shim.HTMLParser is DoclingHtmlParser
        # A reload here used to leave the shared shim on Docling for every later test in the process.
        assert html_parser.HTMLParser is shared
