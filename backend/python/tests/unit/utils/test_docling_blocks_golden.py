"""Golden snapshots of the blocks our converter builds from docling output.

A docling upgrade can change how documents are split (it did for table cells in
2.135). These snapshots pin, for small repo-owned inputs (HTML, Markdown, DOCX):
block types and order, text, table row text and table shape. PDF/OCR need
layout models and are not covered here.

Markdown is pinned through the default parser (MarkdownItParser); the opt-in
docling Markdown path is not snapshotted because it currently flattens the
document (tracked separately).

Regenerate after an intended change: UPDATE_DOCLING_GOLDEN=1 pytest <this file>
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from docling_core.types.doc.document import DoclingDocument

from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
from tests.unit.modules.parsers.real_files.samples import make_docx


@pytest.fixture(autouse=True)
def _fresh_docling_converter():  # noqa: ANN202
    """Other test files build the cached converter while DocumentConverter is patched."""
    from app.modules.parsers.pdf import docling_processor

    docling_processor._get_converter.cache_clear()
    yield
    docling_processor._get_converter.cache_clear()


_GOLDEN_DIR = Path(__file__).parent / "golden"
_LOG = logging.getLogger("docling-golden")

_HTML = b"""<html><body><h1>Team</h1><p>Intro paragraph.</p><table>
<tr><th>Name</th><th>Notes</th></tr>
<tr><td>Priya</td><td>line one<br>line two</td></tr>
<tr><td>Sam</td><td>use <code>kubectl get pods</code> daily</td></tr>
<tr><td>Lee</td><td>plain</td></tr></table>
<ul><li>first</li><li>second</li></ul><p>After table.</p></body></html>"""

_MD = b"""# Release notes

Some **bold** text and a [link](https://example.com).

- item one
- item two

| Item | Qty |
|------|-----|
| apple | 3 |
| pear | 5 |

```python
print("hi")
```
"""

_DOCX_TABLE = [["Region", "Q1", "Q2"], ["North", "1,200", "1,350"], ["東京", "2,000", "2,100"]]


def _summarise(container) -> dict:
    groups = [
        {
            "index": g.index,
            "type": str(g.type.value if hasattr(g.type, "value") else g.type),
            "parent_index": g.parent_index,
            "table": (
                {"rows": g.table_metadata.num_of_rows, "cols": g.table_metadata.num_of_cols}
                if g.table_metadata else None
            ),
        }
        for g in container.block_groups
    ]
    blocks = []
    for b in container.blocks:
        data = b.data
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k in ("row_natural_language_text", "row_number")}
        blocks.append(
            {
                "index": b.index,
                "type": str(b.type.value if hasattr(b.type, "value") else b.type),
                "parent_index": b.parent_index,
                "data": data,
            }
        )
    return {"block_groups": groups, "blocks": blocks}


async def _blocks_from_raw(raw: str):  # noqa: ANN202
    doc = DoclingDocument.model_validate_json(raw)
    return await DoclingProcessor(logger=_LOG, config=None).create_blocks(doc, skip_table_enrichment=True)


async def _html():  # noqa: ANN202
    return await _blocks_from_raw((await DoclingHtmlParser(_LOG, None).parse(_HTML, "t.html")).raw_document)


async def _md():  # noqa: ANN202
    return await MarkdownItParser().parse_to_blocks(_MD.decode("utf-8"))


async def _docx():  # noqa: ANN202
    data = make_docx(
        "Quarterly report",
        ["First paragraph of the report.", "Second paragraph with numbers 1,234 and 56.7%."],
        table=_DOCX_TABLE,
        bullets=["alpha", "beta"],
        closing="End.",
    )
    parsed = await LocalDoclingParser(DoclingProcessor(logger=_LOG, config=None)).parse(data, "t.docx")
    return await _blocks_from_raw(parsed.raw_document)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "build"), [("html_rich_table", _html), ("markdown_it", _md), ("docx_table", _docx)])
async def test_blocks_match_golden(name: str, build) -> None:
    got = _summarise(await build())
    path = _GOLDEN_DIR / f"docling_blocks_{name}.json"
    if os.environ.get("UPDATE_DOCLING_GOLDEN") == "1":
        path.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    want = json.loads(path.read_text(encoding="utf-8"))
    assert got == want, f"docling->blocks output for {name} changed; review the diff and regenerate if intended"


@pytest.mark.asyncio
async def test_rich_cells_are_not_duplicated_as_text_blocks() -> None:
    texts = [b.data for b in (await _html()).blocks if str(b.type.value) == "text"]
    assert "kubectl get pods" not in " ".join(str(t) for t in texts)
