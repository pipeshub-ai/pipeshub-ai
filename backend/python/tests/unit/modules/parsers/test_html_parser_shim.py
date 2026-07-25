"""Full coverage tests for html_parser shim module."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


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
            import app.modules.parsers.html_parser.html_parser as html_parser_module

            importlib.reload(html_parser_module)
            HTMLParser = html_parser_module.HTMLParser

        assert HTMLParser.__name__ == "SelectolaxHtmlParser"
        assert "HTMLParser" in html_parser_module.__all__
        assert "HTMLParserProtocol" in html_parser_module.__all__

    def test_html_parser_defaults_to_selectolax(self):
        with patch.dict("os.environ", {"PARSER_BACKEND": "selectolax"}, clear=False):
            import app.modules.parsers.html_parser.html_parser as html_parser_module

            importlib.reload(html_parser_module)
            HTMLParser = html_parser_module.HTMLParser

        assert HTMLParser.__name__ == "SelectolaxHtmlParser"
        assert HTMLParser.__module__ == (
            "app.modules.parsers.html_parser.selectolax_html_parser"
        )

    def test_html_parser_can_select_docling_backend(self):
        with patch.dict("os.environ", {"PARSER_BACKEND": "docling"}, clear=False):
            with patch(
                "app.modules.parsers.html_parser.docling_html_parser.DocumentConverter"
            ):
                import app.modules.parsers.html_parser.html_parser as html_parser_module

                importlib.reload(html_parser_module)
                HTMLParser = html_parser_module.HTMLParser

        assert HTMLParser.__name__ == "DoclingHtmlParser"
        assert HTMLParser.__module__ == (
            "app.modules.parsers.html_parser.docling_html_parser"
        )
