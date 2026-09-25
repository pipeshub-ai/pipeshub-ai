"""EPUB books built here as real zip files, read without LibreOffice.

LibreOffice can write EPUB but cannot open it, so books are read directly: the
chapters go, in reading order, through the same HTML parser as an HTML upload.
"""

from __future__ import annotations

import base64
import io
import shutil
import struct
import tracemalloc
import zipfile
import zlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.parsing import router as parsing_router
from app.config.constants.arangodb import ExtensionTypes
from app.events.events import DedupDecision, EventProcessor
from app.events.processor import Processor
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlocksContainer, BlockType, GroupType
from app.modules.parsers.epub import epub_reader
from app.modules.parsers.epub.epub_parser import EPUBParser
from app.modules.parsers.epub.epub_reader import read_epub
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.parsing.client import ParsingClient, ParsingClientError
from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
    UnsupportedFormatError,
)
from app.services.parsing.registry import ParserRegistry
from app.utils import user_errors
from app.utils.libreoffice_convert import _run_libreoffice, convert_with_libreoffice
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)


def xhtml(body: str, *, title: str = "page", encoding: str = "utf-8") -> bytes:
    return (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    ).encode(encoding)


def opf(manifest: str, spine: str, *, version: str = "3.0", metadata: str = "", spine_attrs: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}" unique-identifier="uid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">'
        f'<dc:identifier id="uid">urn:uuid:1234</dc:identifier>{metadata}</metadata>'
        f"<manifest>{manifest}</manifest><spine{spine_attrs}>{spine}</spine></package>"
    )


def make_epub(files: dict[str, bytes | str], *, container: bool = True) -> bytes:
    """A zip laid out the way the EPUB spec requires: an uncompressed
    ``mimetype`` entry first, then everything else deflated."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        if container:
            archive.writestr("META-INF/container.xml", CONTAINER)
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def epub3_book() -> bytes:
    # The manifest lists chapter two first; the spine decides the reading order.
    manifest = (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ch2" href="Text/chapter2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ch1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="pic" href="Images/figure.png" media-type="image/png"/>'
    )
    spine = '<itemref idref="nav" linear="no"/><itemref idref="ch1"/><itemref idref="ch2"/>'
    metadata = (
        "<dc:title>The Harbour Book</dc:title><dc:creator>Ada Writer</dc:creator>"
        "<dc:creator>Ben Editor</dc:creator><dc:language>en-GB</dc:language>"
    )
    return make_epub({
        "OEBPS/content.opf": opf(manifest, spine, metadata=metadata),
        "OEBPS/nav.xhtml": xhtml(
            '<nav epub:type="toc"><ol><li><a href="Text/chapter1.xhtml">NavEntryOne</a></li></ol></nav>'
        ),
        "OEBPS/Text/chapter1.xhtml": xhtml(
            "<h1>Chapter One: Arrival</h1><p>The ship reached the harbour at dawn.</p>"
            "<ul><li>Rope</li><li>Anchor</li></ul>"
            '<p>See <a href="chapter2.xhtml#tides">the tides</a> for more.</p>'
            '<p><img src="../Images/figure.png" alt="A map of the harbour"/></p>'
        ),
        "OEBPS/Text/chapter2.xhtml": xhtml(
            '<h1 id="tides">Chapter Two: Tides</h1><p>Tides rise twice a day.</p>'
            "<table><tr><th>Time</th><th>Height</th></tr><tr><td>06:00</td><td>4.2 m</td></tr></table>"
        ),
        "OEBPS/Images/figure.png": PNG_1PX,
    })


def epub2_book() -> bytes:
    manifest = (
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="c1" href="c1.html" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="c2.html" media-type="application/xhtml+xml"/>'
    )
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/>'
        "<docTitle><text>Old Book</text></docTitle><navMap>"
        '<navPoint id="n1" playOrder="1"><navLabel><text>NcxLabelOne</text></navLabel>'
        '<content src="c1.html"/></navPoint></navMap></ncx>'
    )
    return make_epub({
        "OEBPS/content.opf": opf(
            manifest, '<itemref idref="c1"/><itemref idref="c2"/>',
            version="2.0", metadata="<dc:title>Old Book</dc:title><dc:creator>Old Author</dc:creator>",
            spine_attrs=' toc="ncx"',
        ),
        "OEBPS/toc.ncx": ncx,
        "OEBPS/c1.html": xhtml("<h2>Part I</h2><p>An EPUB 2 chapter.</p>"),
        "OEBPS/c2.html": xhtml("<h2>Part II</h2><p>The second chapter.</p>"),
    })


def single_chapter_book(chapter: bytes, *, extra: dict[str, bytes | str] | None = None) -> bytes:
    files: dict[str, bytes | str] = {
        "OEBPS/content.opf": opf(
            '<item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="p" href="Images/p.png" media-type="image/png"/>',
            '<itemref idref="c1"/>',
        ),
        "OEBPS/Text/c1.xhtml": chapter,
    }
    files.update(extra or {})
    return make_epub(files)


def texts(result: ParseResult) -> list[str]:
    return [str(block.data) for block in result.block_container.blocks if block.type == BlockType.TEXT]


def image_blocks(result: ParseResult) -> list:
    return [block for block in result.block_container.blocks if block.type == BlockType.IMAGE]


def _central_directory_offsets(data: bytes, name: str) -> list[int]:
    offsets, pos = [], data.find(b"PK\x01\x02")
    while pos != -1:
        name_len = struct.unpack_from("<H", data, pos + 28)[0]
        if data[pos + 46:pos + 46 + name_len] == name.encode():
            offsets.append(pos)
        pos = data.find(b"PK\x01\x02", pos + 4)
    return offsets


def declare_size(book: bytes, name: str, size: int, crc: int) -> bytes:
    """Rewrite what the zip claims about one entry, in both of its headers."""
    data = bytearray(book)
    local = zipfile.ZipFile(io.BytesIO(book)).getinfo(name).header_offset
    struct.pack_into("<L", data, local + 14, crc)
    struct.pack_into("<L", data, local + 22, size)
    for central in _central_directory_offsets(book, name):
        struct.pack_into("<L", data, central + 16, crc)
        struct.pack_into("<L", data, central + 24, size)
    return bytes(data)


def entry_data_offset(book: bytes, name: str) -> int:
    local = zipfile.ZipFile(io.BytesIO(book)).getinfo(name).header_offset
    name_len, extra_len = struct.unpack_from("<HH", book, local + 26)
    return local + 30 + name_len + extra_len


class _InflateSpy:
    """Records how many bytes each inflate call produced."""

    def __init__(self, real: object, sizes: list[int]) -> None:
        self._real = real
        self._sizes = sizes

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        out = self._real.decompress(data, max_length)
        self._sizes.append(len(out))
        return out

    def flush(self, *args: int) -> bytes:
        out = self._real.flush(*args)
        self._sizes.append(len(out))
        return out

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


async def parse(content: bytes) -> ParseResult:
    return await EPUBParser(SelectolaxHtmlParser()).parse(content, "book.epub")


@pytest.fixture
def no_subprocesses() -> Iterator[AsyncMock]:
    """LibreOffice (or any other program) must never be started for an EPUB."""
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(side_effect=AssertionError("a subprocess was started"))
    ) as spawn:
        yield spawn


@pytest.mark.usefixtures("no_subprocesses")
class TestReadingBooks:
    async def test_epub3_chapters_come_out_in_spine_order_with_their_structure(self) -> None:
        result = await parse(epub3_book())
        text = texts(result)
        joined = "\n".join(text)

        assert joined.index("Chapter One: Arrival") < joined.index("Chapter Two: Tides")
        assert "The ship reached the harbour at dawn." in joined
        assert "[the tides](chapter2.xhtml#tides)" in joined
        assert {"Rope", "Anchor"} <= set(text)
        groups = {group.type for group in result.block_container.block_groups}
        assert {GroupType.LIST, GroupType.TABLE} <= groups
        rows = [b for b in result.block_container.blocks if b.type == BlockType.TABLE_ROW]
        assert rows and "06:00" in str(rows[0].data)
        # The navigation document is a table of contents, not content.
        assert "NavEntryOne" not in joined

    async def test_epub2_with_an_ncx_reads_the_spine_and_not_the_ncx(self) -> None:
        book = read_epub(epub2_book())
        assert book.version == "2.0"
        assert len(book.chapter_bodies) == 2
        joined = "\n".join(texts(await parse(epub2_book())))
        assert joined.index("Part I") < joined.index("Part II")
        assert "NcxLabelOne" not in joined

    async def test_metadata_comes_from_the_package_file(self) -> None:
        book = read_epub(epub3_book())
        assert book.metadata.title == "The Harbour Book"
        assert book.metadata.authors == ["Ada Writer", "Ben Editor"]
        assert book.metadata.language == "en-GB"

        result = await parse(epub3_book())
        assert result.metadata["title"] == "The Harbour Book"
        assert result.metadata["authors"] == ["Ada Writer", "Ben Editor"]
        assert result.metadata["language"] == "en-GB"
        assert result.metadata["chapter_count"] == 2
        assert texts(result)[0] == "The Harbour Book"

    @pytest.mark.parametrize(
        ("declared", "codec", "words"),
        [
            # Tagged Latin-1 but written by Windows: 0x93/0x94 are curly quotes.
            ("iso-8859-1", "cp1252", "Café \u201cquoted\u201d"),
            ("shift_jis", "shift_jis", "日本語の本です"),
            ("windows-1251", "cp1251", "Русская книга"),
        ],
    )
    async def test_a_chapter_in_the_encoding_its_xml_prolog_declares(self, declared, codec, words) -> None:
        chapter = (
            f'<?xml version="1.0" encoding="{declared}"?>'
            f'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>{words}</p></body></html>'
        ).encode(codec)
        assert words in texts(await parse(single_chapter_book(chapter)))

    async def test_chapter_titles_and_notes_in_header_and_footer_are_kept(self) -> None:
        chapter = xhtml(
            "<header><h1>The Lighthouse</h1></header><p>The lamp was lit at dusk.</p>"
            "<footer><p>Note: the keeper retired in 1901.</p></footer>"
        )
        joined = "\n".join(texts(await parse(single_chapter_book(chapter))))
        assert "The Lighthouse" in joined
        assert "Note: the keeper retired in 1901." in joined

    def test_the_navigation_document_is_not_a_chapter(self) -> None:
        book = make_epub({
            "OEBPS/content.opf": opf(
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                '<item id="cover" href="cover.svg" media-type="image/svg+xml" fallback="nav"/>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>',
                '<itemref idref="nav"/><itemref idref="cover"/><itemref idref="c1"/>',
            ),
            "OEBPS/nav.xhtml": xhtml("<p>Contents: NavOnlyText</p>"),
            "OEBPS/cover.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
            "OEBPS/c1.xhtml": xhtml("<p>   </p>"),
        })
        # The table of contents alone does not make a book readable.
        assert _refusal(book).message == user_errors.EPUB_NO_READABLE_CHAPTERS

    async def test_non_linear_items_stay_where_the_spine_puts_them(self) -> None:
        book = make_epub({
            "OEBPS/content.opf": opf(
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>',
                '<itemref idref="c1"/><itemref idref="notes" linear="no"/><itemref idref="c2"/>',
            ),
            "OEBPS/c1.xhtml": xhtml("<p>First chapter.</p>"),
            "OEBPS/notes.xhtml": xhtml("<p>A footnote.</p>"),
            "OEBPS/c2.xhtml": xhtml("<p>Second chapter.</p>"),
        })
        assert texts(await parse(book)) == ["First chapter.", "A footnote.", "Second chapter."]

    async def test_entries_written_with_data_descriptors_are_read(self) -> None:
        class _Unseekable(io.RawIOBase):
            def __init__(self) -> None:
                self.data = io.BytesIO()

            def writable(self) -> bool:
                return True

            def write(self, chunk: bytes) -> int:
                return self.data.write(chunk)

        sink = _Unseekable()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", CONTAINER)
            archive.writestr("OEBPS/content.opf", opf(
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>', '<itemref idref="c1"/>',
            ))
            archive.writestr("OEBPS/c1.xhtml", xhtml("<p>Streamed chapter.</p>"))
        book = sink.data.getvalue()
        assert zipfile.ZipFile(io.BytesIO(book)).getinfo("OEBPS/c1.xhtml").flag_bits & 0x08
        assert "Streamed chapter." in texts(await parse(book))

    async def test_zip64_entries_are_read(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", CONTAINER)
            archive.writestr("OEBPS/content.opf", opf(
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>', '<itemref idref="c1"/>',
            ))
            with archive.open("OEBPS/c1.xhtml", "w", force_zip64=True) as chapter:
                chapter.write(xhtml("<p>Zip64 chapter.</p>"))
        book = buffer.getvalue()
        local = zipfile.ZipFile(io.BytesIO(book)).getinfo("OEBPS/c1.xhtml").header_offset
        name_len, extra_len = struct.unpack_from("<HH", book, local + 26)
        extra = book[local + 30 + name_len:local + 30 + name_len + extra_len]
        assert struct.unpack_from("<H", extra)[0] == 0x0001
        assert "Zip64 chapter." in texts(await parse(book))

    async def test_a_utf16_chapter_with_a_byte_order_mark(self) -> None:
        chapter = xhtml("<p>Sixteen bits</p>", encoding="utf-16")
        assert chapter.startswith((b"\xff\xfe", b"\xfe\xff"))
        assert "Sixteen bits" in texts(await parse(single_chapter_book(chapter)))


@pytest.mark.usefixtures("no_subprocesses")
class TestImages:
    async def test_an_image_in_the_book_becomes_an_image_block(self) -> None:
        with patch(
            "app.modules.parsers.html_parser.selectolax_html_parser.ImageParser.urls_to_base64",
            AsyncMock(side_effect=AssertionError("EPUB images must not be fetched")),
        ):
            result = await parse(epub3_book())
        images = [b for b in result.block_container.blocks if b.type == BlockType.IMAGE]
        assert len(images) == 1
        assert images[0].data["uri"] == "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()

    async def test_an_svg_cover_picture_is_kept(self) -> None:
        chapter = xhtml(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<image xlink:href="../Images/p.png"/></svg><p>Cover</p>'
        )
        result = await parse(single_chapter_book(chapter, extra={"OEBPS/Images/p.png": PNG_1PX}))
        assert any(b.type == BlockType.IMAGE for b in result.block_container.blocks)

    async def test_an_svg_too_large_to_rasterise_is_left_out_without_rasterising(self) -> None:
        huge = "<svg xmlns='http://www.w3.org/2000/svg' width='100000' height='100000'><rect/></svg>"
        chapter = xhtml('<p>Before</p><p><img src="../Images/huge.svg" alt="huge"/></p><p>After</p>')
        small_png = base64.b64encode(PNG_1PX).decode()
        with patch.object(ImageParser, "svg_base64_to_png_base64", return_value=small_png) as rasterise:
            result = await parse(single_chapter_book(chapter, extra={"OEBPS/Images/huge.svg": huge}))
        rasterise.assert_not_called()
        assert not image_blocks(result)
        assert {"Before", "After"} <= set(texts(result))

    @pytest.mark.parametrize("png_bytes", [16, epub_reader.MAX_IMAGE_BYTES + 1])
    async def test_an_svg_whose_png_is_too_big_is_left_out(self, png_bytes: int) -> None:
        svg = "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100'><rect/></svg>"
        chapter = xhtml('<p>Text</p><p><img src="../Images/a.svg" alt="a"/></p>')
        png = base64.b64encode(b"\x89PNG" + b"\0" * (png_bytes - 4)).decode()
        with patch.object(ImageParser, "svg_base64_to_png_base64", return_value=png) as rasterise:
            result = await parse(single_chapter_book(chapter, extra={"OEBPS/Images/a.svg": svg}))
        rasterise.assert_called_once()
        assert len(image_blocks(result)) == (1 if png_bytes <= epub_reader.MAX_IMAGE_BYTES else 0)
        assert "Text" in texts(result)

    async def test_svg_pngs_count_towards_the_books_picture_budget(self) -> None:
        svg = "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100'><rect/></svg>"
        chapter = xhtml("".join(f'<p><img src="../Images/{i}.svg" alt="{i}"/></p>' for i in range(3)) + "<p>Text</p>")
        extra = {f"OEBPS/Images/{i}.svg": svg for i in range(3)}
        png = base64.b64encode(b"\x89PNG" + b"\0" * 1000).decode()
        with patch.object(epub_reader, "MAX_EMBEDDED_IMAGE_BYTES", 2500), \
             patch.object(ImageParser, "svg_base64_to_png_base64", return_value=png):
            result = await parse(single_chapter_book(chapter, extra=extra))
        assert len(image_blocks(result)) == 2

    async def test_images_that_cannot_be_embedded_are_left_out_quietly(self) -> None:
        chapter = xhtml(
            "<p>Before</p>"
            '<p><img src="../Images/missing.png" alt="gone"/></p>'
            '<p><img src="https://example.com/remote.png" alt="remote"/></p>'
            '<p><img src="../../../../etc/secret.png" alt="outside"/></p>'
            '<p><img src="../Images/anim.gif" alt="gif"/></p>'
            "<p>After</p>"
        )
        book = single_chapter_book(chapter, extra={"OEBPS/Images/anim.gif": b"GIF89a"})
        with patch(
            "app.modules.parsers.html_parser.selectolax_html_parser.ImageParser.urls_to_base64",
            AsyncMock(side_effect=AssertionError("EPUB images must not be fetched")),
        ):
            result = await parse(book)
        assert not [b for b in result.block_container.blocks if b.type == BlockType.IMAGE]
        assert {"Before", "After"} <= set(texts(result))
        assert "example.com" not in read_epub(book).to_html()


def _refusal(content: bytes) -> ParseError:
    with pytest.raises(ParseError) as caught:
        read_epub(content)
    return caught.value


@pytest.mark.usefixtures("no_subprocesses")
class TestUnsafeBooks:
    def test_a_zip_bomb_is_refused_before_anything_is_inflated(self) -> None:
        buffer = io.BytesIO()
        chunk = b"\0" * (1024 * 1024)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("META-INF/container.xml", CONTAINER)
            with archive.open("OEBPS/filler.bin", "w") as filler:
                for _ in range(epub_reader.MAX_UNCOMPRESSED_BYTES // len(chunk) + 1):
                    filler.write(chunk)
        assert len(buffer.getvalue()) < 5 * 1024 * 1024

        with patch.object(epub_reader._EpubArchive, "read", side_effect=AssertionError("inflated")):
            error = _refusal(buffer.getvalue())
        assert error.code == ParseErrorCode.INVALID_INPUT
        assert error.message == user_errors.EPUB_TOO_LARGE

    def test_one_huge_chapter_is_refused_without_being_read(self) -> None:
        huge = b"<p>" + b" " * (epub_reader.MAX_CHAPTER_BYTES + 1) + b"</p>"
        book = single_chapter_book(huge)
        real_read = epub_reader._EpubArchive.read
        with patch.object(epub_reader._EpubArchive, "read", autospec=True, side_effect=real_read) as read:
            error = _refusal(book)
        assert error.code == ParseErrorCode.INVALID_INPUT
        assert error.message == user_errors.EPUB_TOO_LARGE
        assert "OEBPS/Text/c1.xhtml" not in [call.args[1].filename for call in read.call_args_list]

    def test_an_entry_that_under_declares_its_size_never_inflates_past_a_chunk(self) -> None:
        prefix = xhtml("<p>Short.</p>")
        stream = 32 * 1024 * 1024
        book = single_chapter_book(prefix + b" " * stream)
        book = declare_size(book, "OEBPS/Text/c1.xhtml", len(prefix), zlib.crc32(prefix))
        assert len(book) < 256 * 1024

        sizes: list[int] = []
        real = zlib.decompressobj
        tracemalloc.start()
        try:
            with patch("zlib.decompressobj", side_effect=lambda *a: _InflateSpy(real(*a), sizes)):
                book_read = read_epub(book)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        # Only what the entry declares is kept, and the rest is never inflated.
        assert book_read.chapter_bodies == ["<p>Short.</p>"]
        assert sizes and max(sizes) <= 64 * 1024
        assert peak < stream // 4

    def test_running_out_of_memory_while_inflating_is_a_final_parse_error(self) -> None:
        class _Exhausted:
            eof = False
            unconsumed_tail = b""

            def decompress(self, data: bytes, max_length: int = 0) -> bytes:
                raise MemoryError

            def flush(self, *args: int) -> bytes:
                raise MemoryError

        with patch("zlib.decompressobj", return_value=_Exhausted()):
            error = _refusal(epub3_book())
        assert error.code == ParseErrorCode.INVALID_INPUT
        assert error.message == user_errors.EPUB_TOO_LARGE

    def test_a_corrupt_deflate_stream_is_a_final_parse_error(self) -> None:
        book = bytearray(epub3_book())
        start = entry_data_offset(bytes(book), "OEBPS/Text/chapter1.xhtml")
        book[start:start + 8] = b"\xff" * 8
        error = _refusal(bytes(book))
        assert error.code == ParseErrorCode.PARSE_FAILED
        assert error.message == user_errors.EPUB_UNREADABLE

    def test_too_many_entries_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("META-INF/container.xml", CONTAINER)
            for index in range(epub_reader.MAX_ENTRIES):
                archive.writestr(f"OEBPS/x{index}.txt", b"")
        error = _refusal(buffer.getvalue())
        assert error.code == ParseErrorCode.INVALID_INPUT
        assert error.message == user_errors.EPUB_TOO_LARGE

    @pytest.mark.parametrize(
        "name", ["OEBPS/../../escape.xhtml", "../escape.xhtml", "/etc/passwd", "C:/Windows/evil.xhtml", "a\\..\\b.xhtml"]
    )
    def test_a_path_that_leaves_the_book_is_refused(self, name: str) -> None:
        book = make_epub({"OEBPS/content.opf": opf("", ""), name: b"x"})
        error = _refusal(book)
        assert error.code == ParseErrorCode.INVALID_INPUT
        assert error.message == user_errors.EPUB_UNSAFE_PATHS

    def test_a_spine_entry_that_points_outside_the_book_is_not_read(self) -> None:
        book = make_epub({
            "OEBPS/content.opf": opf(
                '<item id="c1" href="../../outside.xhtml" media-type="application/xhtml+xml"/>',
                '<itemref idref="c1"/>',
            ),
        })
        assert _refusal(book).message == user_errors.EPUB_NO_READABLE_CHAPTERS


@pytest.mark.usefixtures("no_subprocesses")
class TestBooksThatCannotBeRead:
    def test_a_damaged_zip(self) -> None:
        error = _refusal(epub3_book()[:200])
        assert error.code == ParseErrorCode.PARSE_FAILED
        assert error.message == user_errors.EPUB_UNREADABLE

    def test_bytes_that_are_not_a_zip(self) -> None:
        assert _refusal(b"%PDF-1.7 definitely not a book").message == user_errors.EPUB_UNREADABLE

    def test_an_empty_file(self) -> None:
        assert _refusal(b"").message == user_errors.EPUB_UNREADABLE

    def test_a_zip_that_is_not_an_epub(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<w:document/>")
        error = _refusal(buffer.getvalue())
        assert error.code == ParseErrorCode.PARSE_FAILED
        assert error.message == user_errors.EPUB_UNREADABLE

    def test_a_package_file_that_is_not_xml(self) -> None:
        book = make_epub({"OEBPS/content.opf": b"\x00\x01\x02 garbage"})
        assert _refusal(book).message == user_errors.EPUB_UNREADABLE

    def test_a_drm_protected_book(self) -> None:
        encryption = (
            '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
            '<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>'
            '<enc:CipherData><enc:CipherReference URI="OEBPS/Text/c1.xhtml"/></enc:CipherData>'
            "</enc:EncryptedData></encryption>"
        )
        book = single_chapter_book(b"\x8f\x12 scrambled bytes", extra={"META-INF/encryption.xml": encryption})
        error = _refusal(book)
        assert error.code == ParseErrorCode.PARSE_FAILED
        assert error.message == user_errors.EPUB_COPY_PROTECTED
        assert "copy-protected" in error.message

    async def test_obfuscated_fonts_alone_are_not_drm(self) -> None:
        encryption = (
            '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
            '<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
            '<enc:CipherData><enc:CipherReference URI="OEBPS/Fonts/f.otf"/></enc:CipherData>'
            "</enc:EncryptedData></encryption>"
        )
        book = single_chapter_book(xhtml("<p>Readable</p>"), extra={"META-INF/encryption.xml": encryption})
        assert "Readable" in texts(await parse(book))

    def test_a_book_whose_chapters_are_all_missing(self) -> None:
        book = make_epub({
            "OEBPS/content.opf": opf(
                '<item id="c1" href="Text/gone.xhtml" media-type="application/xhtml+xml"/>',
                '<itemref idref="c1"/>',
            ),
        })
        error = _refusal(book)
        assert error.code == ParseErrorCode.PARSE_FAILED
        assert error.message == user_errors.EPUB_NO_READABLE_CHAPTERS

    @pytest.mark.parametrize(
        "message",
        [
            user_errors.EPUB_UNREADABLE,
            user_errors.EPUB_COPY_PROTECTED,
            user_errors.EPUB_TOO_LARGE,
            user_errors.EPUB_UNSAFE_PATHS,
            user_errors.EPUB_NO_READABLE_CHAPTERS,
        ],
    )
    def test_every_message_is_plain_and_says_what_to_do(self, message: str) -> None:
        for internal in ("zip", "OPF", "spine", "manifest", "XHTML", "container.xml", "Parse"):
            assert internal not in message
        assert any(step in message for step in ("upload", "Upload"))


@pytest.mark.usefixtures("no_subprocesses")
class TestParsingServiceEndToEnd:
    """The parsing service's own route, with the registry parsing_main.py builds for EPUB."""

    def _app(self) -> FastAPI:
        registry = ParserRegistry()
        registry.register("epub", ParserProvider.DEFAULT, EPUBParser(SelectolaxHtmlParser()))
        registry.set_default("epub", ParserProvider.DEFAULT)
        app = FastAPI()
        app.state.parser_registry = registry
        app.state.governor = make_test_governor()
        app.include_router(parsing_router)
        return app

    def _client(self) -> TestClient:
        return TestClient(self._app())

    def _post(self, content: bytes) -> httpx.Response:
        return self._client().post(
            "/api/v1/parse",
            files={"file": ("book.epub", content, "application/epub+zip")},
            data={
                "record_name": "book.epub", "mime_type": "application/epub+zip",
                "extension": "epub", "provider": "default",
            },
        )

    def test_a_real_book_goes_in_and_blocks_come_out(self, no_subprocesses) -> None:
        response = self._post(epub3_book())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        data = " ".join(str(block.get("data")) for block in body["block_container"]["blocks"])
        assert "Chapter One: Arrival" in data and "Tides rise twice a day." in data
        assert body["metadata"]["title"] == "The Harbour Book"
        no_subprocesses.assert_not_called()

    def test_a_damaged_book_is_a_final_parse_failure_with_the_plain_message(self) -> None:
        response = self._post(b"PK\x03\x04 truncated")
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "PARSE_FAILED"
        assert error["message"] == user_errors.EPUB_UNREADABLE

    async def test_the_indexer_asks_once_and_leaves_the_circuit_breaker_alone(self) -> None:
        statuses: list[int] = []

        async def record(response: httpx.Response) -> None:
            statuses.append(response.status_code)

        app = self._app()
        client = ParsingClient(service_url="http://parsing.test", max_retries=3, retry_delay=0.0)
        client._make_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
            transport=httpx.ASGITransport(app=app), event_hooks={"response": [record]}
        )
        with pytest.raises(ParsingClientError) as caught:
            await client.parse(
                file_content=b"PK\x03\x04 truncated", record_name="book.epub",
                mime_type="application/epub+zip", extension="epub", provider=ParserProvider.DEFAULT,
            )
        assert statuses == [422]
        assert not client.circuit_open
        assert client.circuit_breaker._consecutive_failures == 0
        assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TERMINAL
        assert user_errors.to_user_reason(caught.value) == user_errors.EPUB_UNREADABLE

        ok = await client.parse(
            file_content=epub3_book(), record_name="book.epub",
            mime_type="application/epub+zip", extension="epub", provider=ParserProvider.DEFAULT,
        )
        assert any("Chapter One: Arrival" in str(block.data) for block in ok.block_container.blocks)


def _record_dict() -> dict:
    return {
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "book.epub",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext-1",
        "connectorId": "c-1",
        "mimeType": "application/epub+zip",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
    }


@pytest.mark.usefixtures("no_subprocesses")
class TestIndexingServiceEndToEnd:
    """events.py's own EPUB branch, with a real Processor and HTML parser."""

    def _event_processor(self) -> tuple[EventProcessor, AsyncMock]:
        graph_provider = AsyncMock()
        graph_provider.get_document.return_value = _record_dict()
        graph_provider.update_node = AsyncMock(return_value=True)
        with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
            processor = Processor(
                logger=MagicMock(),
                config_service=MagicMock(),
                indexing_pipeline=AsyncMock(),
                graph_provider=graph_provider,
                parsers={ExtensionTypes.HTML.value: SelectolaxHtmlParser()},
                document_extractor=MagicMock(),
                sink_orchestrator=MagicMock(),
            )
        return EventProcessor(MagicMock(), processor, graph_provider, MagicMock()), graph_provider

    def _event(self, content: bytes) -> dict:
        return {
            "payload": {
                "recordId": "rec-1",
                "orgId": "org-1",
                "virtualRecordId": None,
                "version": 1,
                "connectorName": "",
                "extension": ExtensionTypes.EPUB.value,
                "mimeType": "application/epub+zip",
                "recordName": "book.epub",
                "buffer": content,
            }
        }

    async def _run(self, content: bytes) -> tuple[list, BlocksContainer | None]:
        ep, _ = self._event_processor()
        captured = {}

        async def apply(ctx) -> None:
            captured["blocks"] = ctx.record.block_containers

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                          return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch("app.events.processor.IndexingPipeline") as pipeline:
            pipeline.return_value.apply = AsyncMock(side_effect=apply)
            events = [event async for event in ep.on_event(self._event(content))]
        return events, captured.get("blocks")

    async def test_a_real_book_is_indexed_as_blocks(self, no_subprocesses) -> None:
        events, blocks = await self._run(epub3_book())
        assert blocks is not None
        data = " ".join(str(block.data) for block in blocks.blocks)
        assert data.index("Chapter One: Arrival") < data.index("Chapter Two: Tides")
        assert any(block.type == BlockType.IMAGE for block in blocks.blocks)
        assert [e.event for e in events][-1] == "indexing_complete"
        no_subprocesses.assert_not_called()

    async def test_header_and_footer_text_reaches_the_index(self) -> None:
        chapter = xhtml(
            "<header><h1>The Lighthouse</h1></header><p>The lamp was lit at dusk.</p>"
            "<footer><p>Note: the keeper retired in 1901.</p></footer>"
        )
        _, blocks = await self._run(single_chapter_book(chapter))
        data = " ".join(str(block.data) for block in blocks.blocks)
        assert "The Lighthouse" in data
        assert "Note: the keeper retired in 1901." in data

    async def test_a_damaged_book_fails_for_good_with_the_plain_reason(self) -> None:
        with pytest.raises(DocumentProcessingError) as caught:
            await self._run(b"not a book")
        assert caught.value.message == user_errors.EPUB_UNREADABLE
        assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TERMINAL
        assert user_errors.to_user_reason(caught.value) == user_errors.EPUB_UNREADABLE


class TestLibreOfficeIsNotAnEpubReader:
    async def test_it_is_still_never_asked_to_open_an_epub(self, fake_libreoffice, tmp_path: Path) -> None:
        fake_libreoffice()
        with pytest.raises(UnsupportedFormatError):
            await convert_with_libreoffice(epub3_book(), "epub", "pdf")
        assert not (tmp_path / "libreoffice-args.log").exists()

    async def test_other_legacy_formats_still_go_through_it(self, fake_libreoffice, tmp_path: Path) -> None:
        fake_libreoffice(probe_ok=False)
        with pytest.raises(Exception) as caught:
            await convert_with_libreoffice(b"damaged", "ppt", "pptx")
        assert not isinstance(caught.value, ParseError)
        assert (tmp_path / "libreoffice-args.log").exists()

    @pytest.mark.skipif(shutil.which("libreoffice") is None, reason="LibreOffice is not installed")
    async def test_the_real_one_still_cannot_open_epub(self) -> None:
        # Why books are read directly. If a LibreOffice release ever gains an
        # EPUB import filter, this fails and the choice can be revisited.
        run = await _run_libreoffice(epub3_book(), "epub", "pdf")
        assert run.output is None
        assert "could not be loaded" in run.stderr
