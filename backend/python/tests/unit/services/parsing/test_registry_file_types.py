"""The parsing service resolves each new type, under every spelling a caller sends, to its parser."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.email.msg_parser import MsgParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.ooxml.ooxml_package_parser import OoxmlPackageParser
from app.modules.parsers.text.plain_text_parser import PlainTextParser
from app.services.parsing.interface import ParserProvider

if TYPE_CHECKING:
    from app.services.parsing.registry import ParserRegistry


@pytest.fixture(scope="module")
def registry() -> ParserRegistry:
    from app.parsing_main import _build_registry

    return _build_registry(MagicMock(), MagicMock())


_EXPECTED = {
    "msg": (MsgParser, "application/vnd.ms-outlook"),
    "odt": (DocParser, "application/vnd.oasis.opendocument.text"),
    "rtf": (DocParser, "text/rtf"),
    "xlsm": (ExcelParser, "application/vnd.ms-excel.sheet.macroEnabled.12"),
    "log": (PlainTextParser, None),
    "cfg": (PlainTextParser, None),
    "docm": (OoxmlPackageParser, "application/vnd.ms-word.document.macroEnabled.12"),
    "dotx": (OoxmlPackageParser, "application/vnd.openxmlformats-officedocument.wordprocessingml.template"),
    "potx": (OoxmlPackageParser, "application/vnd.openxmlformats-officedocument.presentationml.template"),
}


@pytest.mark.parametrize("provider", [None, ParserProvider.DEFAULT, ParserProvider.DOCLING])
@pytest.mark.parametrize("ext", sorted(_EXPECTED))
def test_each_extension_resolves_whatever_mime_comes_with_it(registry, ext: str, provider) -> None:  # type: ignore[no-untyped-def]
    cls, _ = _EXPECTED[ext]
    # A .log/.cfg stored as text/plain keeps main's text parser (D-16), pinned below.
    mimes = ("application/octet-stream", "", None) if ext in ("log", "cfg") else (
        "application/octet-stream", "text/plain", "", None)
    for mime in mimes:
        for form in (ext, ext.upper(), f".{ext}"):
            assert isinstance(registry.resolve(mime, form, provider), cls), (mime, form)


@pytest.mark.parametrize("ext", [e for e, (_, m) in _EXPECTED.items() if m])
def test_each_mime_resolves_without_an_extension(registry, ext: str) -> None:  # type: ignore[no-untyped-def]
    cls, mime = _EXPECTED[ext]
    for form in (mime, mime.lower(), mime.upper(), f"{mime}; charset=utf-8"):
        assert isinstance(registry.resolve(form, "", None), cls), form


def test_the_libreoffice_parsers_convert_from_their_own_format(registry) -> None:  # type: ignore[no-untyped-def]
    assert registry.resolve("", "odt").input_ext == "odt"
    assert registry.resolve("", "rtf").input_ext == "rtf"
    assert registry.resolve("", "doc").input_ext == "doc"


def test_the_mail_parsers_never_fetch_remote_images(registry) -> None:  # type: ignore[no-untyped-def]
    for provider in (ParserProvider.DEFAULT, ParserProvider.DOCLING):
        assert registry.resolve("", "msg", provider)._html._allow_remote_fetch is False
    # Every other HTML parser still fetches a page's images as before.
    assert registry.resolve("text/html", "html", ParserProvider.DEFAULT)._allow_remote_fetch is True


@pytest.mark.parametrize("ext", ["log", "cfg"])
def test_a_text_plain_log_keeps_main_s_text_parser(registry, ext: str) -> None:  # type: ignore[no-untyped-def]
    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

    assert isinstance(registry.resolve("text/plain", ext, None), MarkdownItParser)
