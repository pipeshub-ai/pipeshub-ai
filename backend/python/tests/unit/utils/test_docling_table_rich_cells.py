"""Table cells with line breaks or inline code must not be indexed twice.

Docling 2.135+ stores such cells in child groups of the table. The table rows
already carry the cell text, so the converter must not emit the children again.
"""

from __future__ import annotations

import logging

import pytest
from docling_core.types.doc.document import DoclingDocument

from app.models.blocks import BlockType
from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor


@pytest.fixture(autouse=True)
def _fresh_docling_converter():  # noqa: ANN202
    """Other test files build the cached converter while DocumentConverter is patched."""
    from app.modules.parsers.pdf import docling_processor

    docling_processor._get_converter.cache_clear()
    yield
    docling_processor._get_converter.cache_clear()


_HTML = b"""<html><body><h1>Team</h1><table>
<tr><th>Name</th><th>Notes</th></tr>
<tr><td>Priya</td><td>line one<br>line two</td></tr>
<tr><td>Sam</td><td>use <code>kubectl get pods</code> daily</td></tr>
<tr><td>Lee</td><td>plain</td></tr></table><p>After table.</p></body></html>"""


@pytest.mark.asyncio
async def test_rich_table_cells_are_not_emitted_as_separate_blocks() -> None:
    log = logging.getLogger("rich-cells")
    parsed = await DoclingHtmlParser(log, None).parse(_HTML, "t.html")
    doc = DoclingDocument.model_validate_json(parsed.raw_document)
    container = await DoclingProcessor(logger=log, config=None).create_blocks(
        doc, skip_table_enrichment=True
    )

    texts = [b.data for b in container.blocks if b.type == BlockType.TEXT]
    rows = [b.data["row_natural_language_text"] for b in container.blocks if b.type == BlockType.TABLE_ROW]

    # Heading and trailing paragraph only: no per-cell text blocks.
    assert texts == ["Team", "After table."]
    assert len(rows) == 4
    assert any("kubectl get pods" in r for r in rows)
    assert any("line one" in r and "line two" in r for r in rows)
    assert len(container.blocks) == 6
    assert len(container.block_groups) == 1


@pytest.mark.asyncio
async def test_picture_inside_a_rich_cell_is_still_emitted() -> None:
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, format="PNG")
    src = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    html = (
        "<html><body><table><tr><th>Name</th><th>Notes</th></tr>"
        f'<tr><td>Priya</td><td>line one<br>line two <img src="{src}" alt="dot"></td></tr>'
        "<tr><td>Lee</td><td>plain</td></tr></table></body></html>"
    ).encode()
    log = logging.getLogger("rich-cells-pic")
    parsed = await DoclingHtmlParser(log, None).parse(html, "t.html")
    doc = DoclingDocument.model_validate_json(parsed.raw_document)
    container = await DoclingProcessor(logger=log, config=None).create_blocks(doc, skip_table_enrichment=True)

    types = [b.type for b in container.blocks]
    assert BlockType.IMAGE in types
    assert not [b for b in container.blocks if b.type == BlockType.TEXT]
    assert types.count(BlockType.TABLE_ROW) == 3
