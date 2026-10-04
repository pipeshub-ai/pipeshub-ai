"""Text-like files saved in the encodings people really use are read correctly."""

from __future__ import annotations

import pytest

from app.models.blocks import BlockSubType
from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.markdown.docling_markdown_parser import DoclingMarkdownParser
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.markdown.mdx_parser import MDXParser
from app.modules.parsers.text_decoding import decode_text

from .samples import all_text

TEXT = "Café menu – naïve “prices”: 5€"


def _utf8_bom(text: str) -> bytes:
    return b"\xef\xbb\xbf" + text.encode("utf-8")


ENCODINGS = [
    pytest.param(lambda t: t.encode("utf-8"), id="utf-8"),
    pytest.param(_utf8_bom, id="utf-8-with-bom"),
    # Windows Notepad "Unicode" and Excel "Unicode Text" both write UTF-16 LE with a BOM.
    pytest.param(lambda t: t.encode("utf-16"), id="utf-16-with-bom"),
    pytest.param(lambda t: b"\xfe\xff" + t.encode("utf-16-be"), id="utf-16-be-with-bom"),
    pytest.param(lambda t: t.encode("cp1252"), id="windows-1252"),
]


@pytest.mark.parametrize("encode", ENCODINGS)
async def test_plain_text(encode) -> None:
    container = (await MarkdownItParser().parse(encode(TEXT + "\n\nSecond paragraph."), "menu.txt")).block_container
    assert [b.data for b in container.blocks] == [TEXT, "Second paragraph."]


@pytest.mark.parametrize("encode", ENCODINGS)
async def test_markdown_heading_survives_a_byte_order_mark(encode) -> None:
    container = (await MarkdownItParser().parse(encode(f"# {TEXT}\n\nBody."), "menu.md")).block_container
    assert container.blocks[0].sub_type == BlockSubType.PARAGRAPH
    assert container.blocks[0].data == f"# {TEXT}\nBody."


@pytest.mark.parametrize("encode", ENCODINGS)
async def test_html(encode) -> None:
    html = f"<html><body><h1>Menu</h1><p>{TEXT}</p></body></html>"
    container = (await SelectolaxHtmlParser().parse(encode(html), "menu.html")).block_container
    assert TEXT in all_text(container)


async def test_html_uses_its_declared_charset() -> None:
    html = '<html><head><meta charset="shift_jis"></head><body><p>東京の価格表</p></body></html>'
    container = (await SelectolaxHtmlParser().parse(html.encode("shift_jis"), "tokyo.html")).block_container
    assert "東京の価格表" in all_text(container)


@pytest.mark.parametrize("declared", ["iso-8859-1", "windows-1252", "latin-1", "gbk"])
async def test_utf8_page_with_a_stale_charset_declaration_stays_utf8(declared: str) -> None:
    # Saved pages and old templates often keep a legacy <meta charset> after
    # being re-saved as UTF-8.
    html = f'<html><head><meta charset="{declared}"></head><body><p>Café in Zürich</p></body></html>'
    assert "Café in Zürich" in decode_text(html.encode("utf-8"), html=True)
    container = (await SelectolaxHtmlParser().parse(html.encode("utf-8"), "page.html")).block_container
    assert "Café in Zürich" in all_text(container)


@pytest.mark.parametrize(
    ("declared", "codec"),
    [("windows-1252", "cp1252"), ("iso-8859-1", "latin-1"), ("koi8-r", "koi8-r")],
)
def test_non_utf8_page_uses_its_matching_declaration(declared: str, codec: str) -> None:
    text = "Привет" if codec == "koi8-r" else "Café"
    html = f'<meta http-equiv="Content-Type" content="text/html; charset={declared}"><p>{text}</p>'
    assert text in decode_text(html.encode(codec), html=True)


async def test_mostly_utf8_file_with_one_damaged_byte_keeps_its_accents() -> None:
    content = "Zoë from Zürich said “hello”".encode() + b"\xff" + " and left.".encode()
    container = (await MarkdownItParser().parse(content, "note.txt")).block_container
    text = all_text(container)
    assert "Zoë from Zürich said “hello”" in text
    assert "and left." in text


@pytest.mark.parametrize("encode", ENCODINGS)
async def test_mdx(encode) -> None:
    container = (await MDXParser(MarkdownItParser()).parse(encode(f"# Menu\n\n{TEXT}\n"), "menu.mdx")).block_container
    assert TEXT in all_text(container)


@pytest.mark.parametrize("encode", ENCODINGS[:4])
async def test_json(encode) -> None:
    content = encode('{"dish": "' + TEXT.replace('"', '\\"') + '"}')
    container = (await JSONParser().parse(content, "menu.json")).block_container
    assert TEXT in str([b.data for b in container.blocks])


async def test_docling_backends_decode_the_same_way(monkeypatch) -> None:
    seen: list[bytes] = []

    class _Doc:
        def model_dump_json(self) -> str:
            return "{}"

    async def fake_parse_document(self: object, name: str, content: bytes) -> _Doc:
        seen.append(content)
        return _Doc()

    monkeypatch.setattr(
        "app.modules.parsers.pdf.docling_processor.DoclingProcessor.parse_document", fake_parse_document
    )
    await DoclingMarkdownParser().parse(TEXT.encode("cp1252"), "menu.md")
    await DoclingHtmlParser().parse(f"<p>{TEXT}</p>".encode("cp1252"), "menu.html")
    assert all(TEXT in content.decode("utf-8") for content in seen)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("﻿already text", "already text"),
        ("naïve".encode("utf-32"), "naïve"),
        (b"\x00\x00\xfe\xff" + "naïve".encode("utf-32-be"), "naïve"),
        # 0x81 is unassigned in Windows-1252; it becomes a replacement mark and
        # the rest of the Windows-1252 text is kept.
        (b"caf\xe9 \x81", "café \ufffd"),
        (b"\x93Hello\x94 \x81", "\u201cHello\u201d \ufffd"),
        (b'<meta charset="no-such-charset"><p>caf\xc3\xa9</p>', '<meta charset="no-such-charset"><p>café</p>'),
    ],
    ids=["str-with-bom", "utf-32-le", "utf-32-be", "cp1252-undefined-byte", "cp1252-curly-quotes", "unknown-declared-charset"],
)
def test_decode_text_edge_cases(content, expected) -> None:
    assert decode_text(content, html=True) == expected



@pytest.mark.parametrize("label", ["iso-8859-1", "ISO-8859-1", "latin1", "latin-1", "l1", "iso8859-1", "cp819", "us-ascii", "ascii"])
def test_windows_1252_page_labelled_with_a_latin1_alias_keeps_its_punctuation(label: str) -> None:
    # The HTML standard reads all of these labels as windows-1252, which is
    # what Word and older editors actually write.
    text = "\u201cHello\u201d \u2013 \u20ac5"
    html = f'<meta charset="{label}"><p>{text}</p>'
    assert text in decode_text(html.encode("cp1252"), html=True)


def test_turkish_page_labelled_iso_8859_9_is_read_as_windows_1254() -> None:
    text = "\u201cMerhaba\u201d \u2013 \u011fü\u015f"
    html = f'<meta charset="iso-8859-9"><p>{text}</p>'
    assert text in decode_text(html.encode("cp1254"), html=True)


async def test_iso_2022_jp_page_is_decoded_with_its_declared_charset() -> None:
    # ISO-2022-JP uses only ASCII bytes, so it also happens to be valid UTF-8.
    html = '<html><head><meta charset="iso-2022-jp"></head><body><p>東京の価格表</p></body></html>'
    content = html.encode("iso-2022-jp")
    assert content.isascii()
    assert "東京の価格表" in decode_text(content, html=True)
    container = (await SelectolaxHtmlParser().parse(content, "tokyo.html")).block_container
    assert "東京の価格表" in all_text(container)


def test_utf8_page_with_a_stale_iso_2022_jp_label_stays_utf8() -> None:
    html = '<meta charset="iso-2022-jp"><p>Café in Zürich</p>'
    assert "Café in Zürich" in decode_text(html.encode("utf-8"), html=True)
