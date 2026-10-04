"""Empty, damaged, locked and very large files end in a clear, recorded outcome.

A parser that raises ``ParseError`` makes the parsing service answer 422, and the
record is marked failed with a reason. An empty block container makes the
indexer mark the record EMPTY. Any other exception becomes a 500, which the
indexer treats as an outage: it retries the file and counts it against the
parsing circuit breaker, so a handful of bad files can stall everyone else.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote, urlparse

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.xls_parser import XLSParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.modules.parsers.pptx.ppt_parser import PPTParser
from app.modules.parsers.text_splitting import MAX_TEXT_BLOCK_CHARS
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
from app.services.parsing.providers.smart_pdf_parser import SmartPDFParser
from app.utils.libreoffice_convert import (
    LibreOfficeCouldNotReadFileError,
    convert_with_libreoffice,
)

from .samples import all_text, make_docx, make_pdf, make_pptx, table_rows

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _no_llm(module: str) -> AbstractContextManager[AsyncMock]:
    return patch(f"{module}.get_llm_for_role", AsyncMock(return_value=(MagicMock(), {})))


class TestOfficeFilesThroughDocling:
    @pytest.mark.parametrize("name", ["report.docx", "deck.pptx"])
    @pytest.mark.parametrize(
        "content",
        [b"", b"this is not an office file", OLE_SIGNATURE + b"\0" * 1024],
        ids=["empty", "garbage", "old-binary-container"],
    )
    async def test_unreadable_file_is_a_parse_error(self, logger, name: str, content: bytes) -> None:
        parser = LocalDoclingParser(DoclingProcessor(logger, None))
        with pytest.raises(ParseError) as caught:
            await parser.parse(content, name)
        assert caught.value.code == ParseErrorCode.PARSE_FAILED

    @pytest.mark.parametrize(
        ("name", "build"),
        [("report.docx", lambda: make_docx("Title", ["Body text"] * 20)),
         ("deck.pptx", lambda: make_pptx([("Slide", "Body")] * 5))],
    )
    async def test_truncated_file_is_a_parse_error(self, logger, name: str, build) -> None:
        parser = LocalDoclingParser(DoclingProcessor(logger, None))
        with pytest.raises(ParseError):
            await parser.parse(build()[:400], name)


class TestEmptyTextFiles:
    @pytest.mark.parametrize("content", [b"", b"   \n\n\t  \n"], ids=["empty", "whitespace"])
    async def test_markdown_and_text_give_an_empty_container(self, content: bytes) -> None:
        result = await MarkdownItParser().parse(content, "notes.txt")
        assert result.block_container.blocks == []
        assert result.block_container.block_groups == []

    @pytest.mark.parametrize(
        "content",
        [b"", b"<html><body>   </body></html>", b"<html><head><script>x()</script></head></html>"],
        ids=["empty", "blank-body", "script-only"],
    )
    async def test_html_without_visible_text_gives_an_empty_container(self, content: bytes) -> None:
        result = await SelectolaxHtmlParser().parse(content, "page.html")
        assert result.block_container.blocks == []

    @pytest.mark.parametrize("content", [b"", b"  \n "])
    async def test_empty_json_is_reported_as_empty(self, content: bytes) -> None:
        with pytest.raises(ParseError) as caught:
            await JSONParser().parse(content, "data.json")
        assert caught.value.code == ParseErrorCode.EMPTY_CONTENT

    async def test_broken_json_is_a_parse_error(self) -> None:
        with pytest.raises(ParseError) as caught:
            await JSONParser().parse(b'{"a": 1,', "data.json")
        assert caught.value.code == ParseErrorCode.PARSE_FAILED


class TestHugeText:
    async def test_huge_paragraph_is_split_without_losing_words(self) -> None:
        words = [f"word{i}" for i in range(40_000)]
        text = " ".join(words)
        assert len(text) > 3 * MAX_TEXT_BLOCK_CHARS
        container = (await MarkdownItParser().parse(text.encode(), "big.txt")).block_container
        assert len(container.blocks) > 1
        assert all(len(b.data) <= MAX_TEXT_BLOCK_CHARS for b in container.blocks)
        assert all_text(container).split() == words

    async def test_huge_html_paragraph_is_split_without_losing_words(self) -> None:
        words = [f"w{i}." for i in range(30_000)]
        html = f"<html><body><p>{' '.join(words)}</p></body></html>".encode()
        container = (await SelectolaxHtmlParser().parse(html, "big.html")).block_container
        assert len(container.blocks) > 1
        assert all(len(b.data) <= MAX_TEXT_BLOCK_CHARS for b in container.blocks)
        assert all_text(container).split() == words


# ---------------------------------------------------------------------------
# Known bugs in files that open pull requests are changing. Each test states the
# correct behaviour and is expected to fail until the bug is fixed there.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="csv_parser.py: a cell over Python's 128 KB csv field limit makes parse() "
    "swallow the error and return an empty container, so the file is marked EMPTY",
)
async def test_csv_with_a_very_long_cell_is_not_silently_empty() -> None:
    content = ("id,notes\n1," + "x" * 200_000 + "\n2,short\n").encode()
    with _no_llm("app.modules.parsers.csv.csv_parser"):
        try:
            result = await CSVParser(config_service=MagicMock()).parse(content, "big.csv")
        except ParseError:
            return
    assert result.block_container.blocks, "a CSV with real rows was indexed as empty"


@pytest.mark.xfail(
    strict=True,
    reason="csv_parser.py: convert_table_to_dict cuts every row to the header width, "
    "so values past the last header are dropped (the no-LLM path keeps them)",
)
def test_csv_row_longer_than_its_header_keeps_every_value() -> None:
    parser = CSVParser(config_service=MagicMock())
    rows, _ = parser.convert_table_to_dict(
        {"headers": ["name", "amount"], "data": [["Ann", "20", "surprise-extra"]], "start_row": 1}
    )
    assert "surprise-extra" in [str(v) for v in rows[0].values()]


@pytest.mark.xfail(
    strict=True,
    reason="excel_parser.py: a damaged or non-xlsx file raises zipfile.BadZipFile out of "
    "parse(), which the parsing service reports as a 500 outage instead of a parse error",
)
@pytest.mark.parametrize(
    "content", [b"not a workbook", OLE_SIGNATURE + b"\0" * 512], ids=["garbage", "old-binary-container"]
)
async def test_unreadable_xlsx_is_a_parse_error(logger, content: bytes) -> None:
    with _no_llm("app.modules.parsers.excel.excel_parser"):
        with pytest.raises(ParseError):
            await ExcelParser(logger, MagicMock()).parse(content, "book.xlsx")


@pytest.mark.xfail(
    strict=True,
    reason="excel_parser.py: an empty .xlsx upload fails an internal assert (a 500) "
    "instead of being reported as empty",
)
async def test_empty_xlsx_is_reported_as_empty(logger) -> None:
    with _no_llm("app.modules.parsers.excel.excel_parser"):
        try:
            result = await ExcelParser(logger, MagicMock()).parse(b"", "book.xlsx")
        except ParseError as exc:
            assert exc.code == ParseErrorCode.EMPTY_CONTENT
            return
    assert result.block_container.blocks == []


@pytest.mark.xfail(
    strict=True,
    reason="smart_pdf_parser.py: a password-protected PDF is not recognised; after the "
    "main parser fails it is sent to OCR, which spends AI-model calls on a file no one can open",
)
async def test_password_protected_pdf_is_reported_and_not_sent_to_ocr() -> None:
    primary = MagicMock()
    primary.parse = AsyncMock(side_effect=ParseError(ParseErrorCode.PARSE_FAILED, "cannot open"))
    ocr = MagicMock()
    ocr.parse = AsyncMock(return_value=MagicMock(spec=ParseResult))
    content = make_pdf([["Confidential salaries"]], encrypt="s3cret")

    with pytest.raises(ParseError) as caught:
        await SmartPDFParser(primary, ocr).parse(content, "salaries.pdf")
    assert "password" in caught.value.message.lower()
    ocr.parse.assert_not_called()


@pytest.mark.xfail(
    strict=True,
    reason="image_parser.py: an empty image file becomes an image block with no data "
    "instead of being reported as empty",
)
async def test_empty_image_is_reported_as_empty(logger) -> None:
    with pytest.raises(ParseError) as caught:
        await ImageParser(logger).parse(b"", "photo.png", {"extension": "png"})
    assert caught.value.code == ParseErrorCode.EMPTY_CONTENT


@pytest.mark.xfail(
    strict=True,
    reason="csv_parser.py: a CSV saved as 'CSV UTF-8' by Excel starts with a byte-order "
    "mark, which stays glued to the first column name",
)
async def test_csv_byte_order_mark_is_not_part_of_the_first_header() -> None:
    content = "﻿Name,City\nZoë,Zürich\n".encode()
    container = await CSVParser(config_service=MagicMock()).parse_to_blocks_lightweight(content)
    assert table_rows(container) == ["Name: Zoë, City: Zürich"]


@pytest.mark.xfail(
    strict=True,
    reason="csv_parser.py: Windows-1252 files are decoded as Latin-1, which turns curly "
    "quotes and dashes into invisible control characters",
)
async def test_windows_1252_csv_keeps_its_punctuation() -> None:
    content = "item,note\nWidget,“best” – top seller\n".encode("cp1252")
    container = await CSVParser(config_service=MagicMock()).parse_to_blocks_lightweight(content)
    assert "“best” – top seller" in table_rows(container)[0]


LEGACY_PARSERS = [
    pytest.param(lambda: DocParser(MagicMock()), "memo.doc", id="doc"),
    pytest.param(lambda: PPTParser(MagicMock()), "deck.ppt", id="ppt"),
    pytest.param(lambda: XLSParser(MagicMock()), "book.xls", id="xls"),
]


class TestLegacyFilesLibreOfficeCannotRead:
    @pytest.mark.parametrize(("make_parser", "name"), LEGACY_PARSERS)
    async def test_is_a_parse_error_not_a_server_error(self, fake_libreoffice, make_parser, name: str) -> None:
        fake_libreoffice()
        with pytest.raises(ParseError) as caught:
            await make_parser().parse(b"\x00\x01 not really an office file", name)
        assert caught.value.code == ParseErrorCode.PARSE_FAILED
        assert "could not be loaded" in caught.value.details.get("stderr", "")

    @pytest.mark.parametrize(("make_parser", "name"), LEGACY_PARSERS)
    async def test_clean_exit_without_output_is_a_parse_error(self, fake_libreoffice, make_parser, name: str) -> None:
        fake_libreoffice(body="exit 0\n")
        with pytest.raises(ParseError) as caught:
            await make_parser().parse(b"\x00\x01 not really an office file", name)
        assert caught.value.code == ParseErrorCode.PARSE_FAILED

    @pytest.mark.skipif(shutil.which("libreoffice") is None, reason="LibreOffice is not installed")
    @pytest.mark.parametrize(("make_parser", "name"), [LEGACY_PARSERS[1]])
    async def test_with_the_real_libreoffice(self, make_parser, name: str) -> None:
        with pytest.raises(ParseError) as caught:
            await make_parser().parse(OLE_SIGNATURE + b"\0" * 600, name)
        assert caught.value.code == ParseErrorCode.PARSE_FAILED


class TestLibreOfficeProblemsStayRetryable:
    """A LibreOffice that is broken, busy or out of disk is not the file's fault.
    Those must stay ordinary errors, which the indexer retries, not parse errors,
    which mark the file failed for good."""

    @pytest.mark.parametrize(
        "body",
        [
            "echo 'User installation could not be completed' >&2\nexit 1\n",
            "echo 'Error: Please verify input parameters...' >&2\nexit 1\n",
            "exit 81\n",
            "kill -9 $$\n",
        ],
        ids=["profile-error", "could-not-write-output", "restart-requested", "killed-by-signal"],
    )
    @pytest.mark.parametrize(("make_parser", "name"), LEGACY_PARSERS)
    async def test_is_not_a_parse_error(self, fake_libreoffice, make_parser, name: str, body: str) -> None:
        fake_libreoffice(body=body)
        with pytest.raises(DocumentProcessingError) as caught:
            await make_parser().parse(b"real office bytes", name)
        assert not isinstance(caught.value, ParseError)

    async def test_signal_is_named_in_the_error(self, fake_libreoffice) -> None:
        fake_libreoffice(body="kill -9 $$\n")
        with pytest.raises(DocumentProcessingError) as caught:
            await convert_with_libreoffice(b"x", "doc", "docx")
        assert "signal 9" in str(caught.value)

    async def test_each_conversion_gets_its_own_profile(self, fake_libreoffice, tmp_path) -> None:
        # Conversions that share one LibreOffice profile lock each other out,
        # and a locked-out run exits cleanly having written nothing.
        fake_libreoffice(body="exit 0\n")
        for _ in range(2):
            with pytest.raises(LibreOfficeCouldNotReadFileError):
                await convert_with_libreoffice(b"x", "doc", "docx")

        log = tmp_path / "libreoffice-args.log"
        runs = _runs(log)
        assert _probe_runs(log), "the probe conversions should also have run"
        profiles = []
        for args in runs:
            profile = next(a for a in args if a.startswith("-env:UserInstallation="))
            outdir = Path(args[args.index("--outdir") + 1])
            profile_path = Path(unquote(urlparse(profile.split("=", 1)[1]).path))
            assert profile_path.parent == outdir
            profiles.append(profile_path)
        assert len(runs) >= 3
        assert len(set(profiles)) == len(profiles)

    @pytest.mark.skipif(shutil.which("libreoffice") is None, reason="LibreOffice is not installed")
    async def test_real_conversions_run_side_by_side(self) -> None:
        docs = [make_docx(f"Report {i}", [f"Body of report {i}"]) for i in range(3)]
        pdfs = await asyncio.gather(*(convert_with_libreoffice(d, "docx", "pdf") for d in docs))
        assert all(p.startswith(b"%PDF") for p in pdfs)



def _runs(log: Path) -> list[list[str]]:
    return [r.split("\n") for r in log.read_text().split("---\n") if r.strip()]


def _probe_runs(log: Path) -> list[list[str]]:
    return [args for args in _runs(log) if any(Path(a).name.startswith("probe.") for a in args)]


class TestFormatSupportProbe:
    """LibreOffice prints "source file could not be loaded" for a damaged file,
    but also when the component that reads that format is not installed or the
    load hits an I/O error. The file is blamed only once LibreOffice has shown
    it can load a known-good file of the same format on this host."""

    @pytest.mark.parametrize(("make_parser", "name"), LEGACY_PARSERS)
    async def test_file_is_not_blamed_when_the_format_cannot_be_loaded_at_all(
        self, fake_libreoffice, make_parser, name: str, caplog
    ) -> None:
        fake_libreoffice(probe_ok=False)
        with caplog.at_level(logging.WARNING), pytest.raises(DocumentProcessingError) as caught:
            await make_parser().parse(b"real office bytes", name)
        assert not isinstance(caught.value, (ParseError, LibreOfficeCouldNotReadFileError))
        assert "component" in caplog.text and f".{name.rsplit('.', 1)[1]}" in caplog.text

    async def test_format_without_a_probe_stays_retryable_without_a_false_warning(
        self, fake_libreoffice, caplog, tmp_path
    ) -> None:
        fake_libreoffice()
        with caplog.at_level(logging.DEBUG), pytest.raises(DocumentProcessingError) as caught:
            await convert_with_libreoffice(b"{\\rtf1 damaged", "rtf", "docx")
        assert not isinstance(caught.value, LibreOfficeCouldNotReadFileError)
        assert "component" not in caplog.text
        assert not _probe_runs(tmp_path / "libreoffice-args.log")

    @pytest.mark.parametrize(("make_parser", "name"), LEGACY_PARSERS)
    async def test_file_is_blamed_after_the_probe_passes(self, fake_libreoffice, make_parser, name: str, tmp_path) -> None:
        fake_libreoffice(probe_ok=True)
        with pytest.raises(ParseError):
            await make_parser().parse(b"damaged bytes", name)
        assert _probe_runs(tmp_path / "libreoffice-args.log")

    async def test_probe_runs_once_per_format(self, fake_libreoffice, tmp_path) -> None:
        fake_libreoffice(probe_ok=True)
        with pytest.raises(LibreOfficeCouldNotReadFileError):
            await convert_with_libreoffice(b"damaged", "ppt", "pptx")
        first = len(_probe_runs(tmp_path / "libreoffice-args.log"))
        assert first >= 1
        for _ in range(2):
            with pytest.raises(LibreOfficeCouldNotReadFileError):
                await convert_with_libreoffice(b"damaged", "ppt", "pptx")
        assert len(_probe_runs(tmp_path / "libreoffice-args.log")) == first
        with pytest.raises(LibreOfficeCouldNotReadFileError):
            await convert_with_libreoffice(b"damaged", "xls", "xlsx")
        assert len(_probe_runs(tmp_path / "libreoffice-args.log")) > first
        runs = len(_runs(tmp_path / "libreoffice-args.log"))
        with pytest.raises(LibreOfficeCouldNotReadFileError):
            await convert_with_libreoffice(b"damaged", "xls", "xlsx")
        assert len(_runs(tmp_path / "libreoffice-args.log")) == runs + 1


class TestCleanExitWithoutOutput:
    async def test_other_diagnostics_stay_retryable_and_are_kept(self, fake_libreoffice) -> None:
        fake_libreoffice(body="echo 'convert input.doc as a Writer document'\necho 'Error: disk full while saving' >&2\nexit 0\n")
        with pytest.raises(DocumentProcessingError) as caught:
            await convert_with_libreoffice(b"x", "doc", "docx")
        assert not isinstance(caught.value, LibreOfficeCouldNotReadFileError)
        assert "disk full" in caught.value.details["stderr"]
        assert "Writer document" in caught.value.details["stdout"]

    async def test_no_diagnostics_and_a_passing_probe_blames_the_file(self, fake_libreoffice) -> None:
        fake_libreoffice(body="echo 'convert input.doc as a Writer document'\nexit 0\n")
        with pytest.raises(LibreOfficeCouldNotReadFileError) as caught:
            await convert_with_libreoffice(b"x", "doc", "docx")
        assert caught.value.details["stderr"] == ""
        assert "Writer document" in caught.value.details["stdout"]

    async def test_no_diagnostics_and_a_failing_probe_stays_retryable(self, fake_libreoffice) -> None:
        fake_libreoffice(body="exit 0\n", probe_ok=False)
        with pytest.raises(DocumentProcessingError) as caught:
            await convert_with_libreoffice(b"x", "doc", "docx")
        assert not isinstance(caught.value, LibreOfficeCouldNotReadFileError)
