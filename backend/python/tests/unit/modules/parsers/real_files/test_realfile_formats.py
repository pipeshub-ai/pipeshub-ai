"""Each supported format, parsed from a real file, keeps its text, tables and structure."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlocksContainer, BlockSubType, BlockType, GroupType
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser

from .samples import (
    all_text,
    make_docx,
    make_pdf,
    make_png,
    make_pptx,
    make_xlsx,
    table_rows,
)

if TYPE_CHECKING:
    import logging


async def _docling_blocks(logger: logging.Logger, name: str, content: bytes) -> BlocksContainer:
    """Parse with the in-process Docling provider, then build blocks the way the
    indexing service does after the parsing service returns a raw document."""
    processor = DoclingProcessor(logger, None)
    result = await LocalDoclingParser(processor).parse(content, name)
    assert result.raw_document and result.block_container is None
    from docling_core.types.doc.document import DoclingDocument

    document = DoclingDocument.model_validate_json(result.raw_document)
    return await processor.create_blocks(document, skip_table_enrichment=True)


class TestDocx:
    async def test_text_table_and_order_survive(self, logger) -> None:
        content = make_docx(
            "Quarterly Report",
            ["Revenue grew by twelve percent."],
            table=[("Region", "Sales"), ("North", "100"), ("South", "200"), ("East", "300")],
            bullets=["Hire two engineers"],
            closing="Closing remarks after the table.",
        )
        container = await _docling_blocks(logger, "report.docx", content)
        text = all_text(container)

        for expected in ("Quarterly Report", "Revenue grew by twelve percent.",
                         "Hire two engineers", "Closing remarks after the table."):
            assert expected in text
        rows = table_rows(container)
        assert len(rows) == 4
        assert "North" in rows[1] and "100" in rows[1]
        assert "East" in rows[3] and "300" in rows[3]
        assert text.index("Revenue grew") < text.index("North") < text.index("Closing remarks")
        tables = [g for g in container.block_groups if g.type == GroupType.TABLE]
        assert len(tables) == 1
        assert tables[0].table_metadata.num_of_rows == 4
        assert tables[0].table_metadata.num_of_cols == 2

    async def test_long_table_is_not_truncated(self, logger) -> None:
        table = [("Id", "Name")] + [(str(i), f"person-{i}") for i in range(120)]
        container = await _docling_blocks(logger, "people.docx", make_docx("People", [], table=table))
        rows = table_rows(container)
        assert len(rows) == 121
        assert "person-119" in rows[-1]


class TestPptx:
    async def test_every_slide_and_table_cell_is_kept(self, logger) -> None:
        content = make_pptx(
            [("Roadmap", "Ship search v2"), ("Risks", "Hiring is slow")],
            table=[("Quarter", "Revenue"), ("Q1", "10"), ("Q2", "20")],
        )
        container = await _docling_blocks(logger, "deck.pptx", content)
        text = all_text(container)
        for expected in ("Roadmap", "Ship search v2", "Risks", "Hiring is slow"):
            assert expected in text
        assert text.index("Roadmap") < text.index("Risks")
        rows = table_rows(container)
        assert len(rows) == 3
        assert "Q2" in rows[2] and "20" in rows[2]


class TestXlsx:
    async def test_every_sheet_row_and_column_is_kept(self, logger) -> None:
        wide_header = [f"col{i}" for i in range(30)]
        content = make_xlsx({
            "Sales": [("Region", "Q1", "Q2")] + [(f"R{i}", i, i * 2) for i in range(250)],
            "Wide": [wide_header, [f"v{i}" for i in range(30)]],
            "Staff": [("Name", "Role"), ("Zoë", "Directrice générale")],
        })
        container = await ExcelParser(logger, MagicMock()).parse_workbook(content)

        sheets = [g for g in container.block_groups if g.type == GroupType.SHEET]
        assert [g.name for g in sheets] == ["Sales", "Wide", "Staff"]
        rows = table_rows(container)
        assert len(rows) == 250 + 1 + 1
        assert rows[249] == "Region: R249, Q1: 249, Q2: 498"
        assert "col29: v29" in rows[250]
        assert rows[251] == "Name: Zoë, Role: Directrice générale"


class TestCsvWithoutLlm:
    """The no-LLM CSV path used for chat attachments."""

    async def test_quoting_is_honoured_and_no_value_is_dropped(self) -> None:
        content = (
            'name,notes,amount\n'
            '"Smith, John","line one\nline two",10\n'
            'Ann,plain,20,surprise-extra\n'
        ).encode()
        container = await CSVParser(config_service=MagicMock()).parse_to_blocks_lightweight(content)
        rows = table_rows(container)
        assert len(rows) == 2
        assert "name: Smith, John" in rows[0]
        assert "line one\nline two" in rows[0]
        assert "surprise-extra" in rows[1]

    async def test_row_cap_applies_only_past_the_limit(self) -> None:
        content = ("id\n" + "\n".join(str(i) for i in range(300))).encode()
        container = await CSVParser(config_service=MagicMock()).parse_to_blocks_lightweight(content)
        assert len(table_rows(container)) == 300


class TestHtml:
    async def test_structure_text_and_tables(self) -> None:
        html = b"""<html><head><title>t</title><style>.x{color:red}</style>
        <script>var secret = 1;</script></head><body>
        <h1>Employee Handbook</h1>
        <p>Welcome &amp; thanks for joining.</p>
        <ul><li>Laptop</li><li>Badge</li></ul>
        <table><tr><th>Name</th><th>Days</th></tr><tr><td>Ann</td><td>5</td></tr>
        <tr><td>Bob</td><td>7</td></tr></table>
        <pre>line 1
line 2</pre></body></html>"""
        result = await SelectolaxHtmlParser().parse(html, "handbook.html")
        container = result.block_container
        text = all_text(container)

        assert "Employee Handbook" in text
        assert "Welcome & thanks for joining." in text
        assert "Laptop" in text and "Badge" in text
        assert "secret" not in text and "color:red" not in text
        assert table_rows(container) == ["Name: Ann, Days: 5", "Name: Bob, Days: 7"]
        assert any(b.sub_type == BlockSubType.CODE and "line 1\nline 2" in b.data for b in container.blocks)


class TestMarkdownAndText:
    async def test_markdown_structure(self) -> None:
        md = (
            "# Runbook\n\nRestart the service.\n\n"
            "- step one\n- step two\n\n"
            "```bash\nsystemctl restart app\n```\n\n"
            "| Env | Owner |\n|---|---|\n| prod | Ann |\n| stage | Bob |\n"
        ).encode()
        container = (await MarkdownItParser().parse(md, "runbook.md")).block_container

        first = container.blocks[0]
        assert first.sub_type == BlockSubType.PARAGRAPH
        assert first.data == "# Runbook\nRestart the service."
        list_items = [b.data for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        assert list_items == ["step one", "step two"]
        code = [b for b in container.blocks if b.sub_type == BlockSubType.CODE]
        assert code[0].data == "systemctl restart app"
        assert code[0].code_metadata.language == "bash"
        assert table_rows(container) == ["Env: prod, Owner: Ann", "Env: stage, Owner: Bob"]

    async def test_plain_text_keeps_every_word(self) -> None:
        text = (
            "Meeting notes 2026-09-01\n\n"
            "Attendees: Ann, Bob\n"
            "Decision: move launch to Q4 * pending legal\n\n"
            "Action items follow.\n"
        )
        container = (await MarkdownItParser().parse(text.encode(), "notes.txt")).block_container
        assert all_text(container).split() == text.split()


class TestPdf:
    async def test_text_from_every_page_with_page_numbers(self, logger) -> None:
        content = make_pdf([["Invoice 1042", "Total due: 950 EUR"], ["Page two terms", "Net 30 days"]])
        parser = PdfPlumberParser(PDFPlumberOpenCVProcessor(logger, MagicMock()))
        with patch(
            "app.modules.parsers.pdf.pdfplumber_opencv_processor.get_llm_for_role",
            AsyncMock(side_effect=AssertionError("a text-only PDF must not need an LLM")),
        ):
            container = (await parser.parse(content, "invoice.pdf")).block_container

        text = all_text(container)
        for expected in ("Invoice 1042", "Total due: 950 EUR", "Page two terms", "Net 30 days"):
            assert expected in text
        pages = {
            b.citation_metadata.page_number
            for b in container.blocks
            if b.type == BlockType.TEXT and b.citation_metadata
        }
        assert pages == {1, 2}


class TestImage:
    async def test_image_becomes_one_block_with_its_bytes(self, logger) -> None:
        png = make_png()
        result = await ImageParser(logger).parse(png, "chart.png", {"extension": "png"})
        blocks = result.block_container.blocks
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.IMAGE
        prefix = "data:image/png;base64,"
        uri = blocks[0].data["uri"]
        assert uri.startswith(prefix)
        assert base64.b64decode(uri[len(prefix):]) == png


@pytest.mark.parametrize("extension", ["svg"])
async def test_svg_is_rasterised_to_png(logger, extension: str) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4" fill="red"/></svg>'
    result = await ImageParser(logger).parse(svg, "logo.svg", {"extension": extension})
    uri = result.block_container.blocks[0].data["uri"]
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n"
