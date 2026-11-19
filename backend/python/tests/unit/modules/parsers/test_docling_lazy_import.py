"""``docling`` is imported when a document needs it, not when a service starts.

The first test blocks the library entirely and runs the indexing path for PDFs and the
parser set; the second runs the real library in a fresh interpreter and checks that the
import and the conversion happen off the event loop's thread (scenarios T3, T5, T11).
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[4]


def _run(script: str, timeout: int) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n{textwrap.dedent(script)}"],
        cwd=BACKEND, capture_output=True, text=True, timeout=timeout,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


_BLOCK_DOCLING = """
import importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "docling" or name.startswith("docling."):
            raise ImportError("docling is blocked in this test: " + name)
sys.meta_path.insert(0, Block())
"""


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_indexing_pdf_path_and_the_parser_set_build_without_docling() -> None:
    result = _run(
        _BLOCK_DOCLING
        + """
import asyncio, json, logging
from unittest.mock import MagicMock
from docling_core.types.doc import DocItemLabel
from docling_core.types.doc.document import DoclingDocument

from app.containers.utils.utils import ContainerUtils
from app.events.processor import Processor
from app.modules.parsers.pdf.docling_processor import DoclingProcessor

logger = logging.getLogger("t")
config = MagicMock()

# The indexing process builds its parser set and the Processor at start-up.
parsers = asyncio.run(ContainerUtils().create_parsers(logger, config))
processor = Processor(logger, config, MagicMock(), MagicMock(), parsers, MagicMock(), MagicMock())
assert isinstance(processor.docling_processor, DoclingProcessor)

# A PDF is parsed by the docling service; indexing only turns the stored document into blocks.
doc = DoclingDocument(name="stored")
doc.add_text(label=DocItemLabel.TEXT, text="hello from a stored document")
blocks = asyncio.run(processor.docling_processor.create_blocks(doc, skip_table_enrichment=True))
texts = [str(b.data) for b in blocks.blocks]
print("RESULT " + json.dumps({"texts": texts, "docling": [m for m in sys.modules if m == "docling" or m.startswith("docling.")]}))
""",
        timeout=280,
    )
    assert any("hello from a stored document" in t for t in result["texts"])
    assert result["docling"] == []


_REAL_DOCLING = """
import asyncio, importlib.abc, json, logging, os, threading
from io import BytesIO
from unittest.mock import MagicMock

# Read at import: above 1 it sends the conversion to a process pool, where this script cannot see its thread.
os.environ.pop("LOCAL_DOCLING_PARSE_WORKERS", None)
loop_thread = threading.get_ident()
imported_on = []
converted_on = []
class Watch(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "docling" and not imported_on:
            imported_on.append(threading.get_ident())
        return None
sys.meta_path.insert(0, Watch())

import docx
from app.modules.parsers.pdf.docling_processor import DoclingProcessor

def make_docx():
    d = docx.Document()
    d.add_heading("Quarterly plan", level=1)
    d.add_paragraph("The first paragraph of the plan.")
    buf = BytesIO()
    d.save(buf)
    return buf.getvalue()

async def main():
    processor = DoclingProcessor(logging.getLogger("t"), MagicMock())
    from app.utils.lazy_heavy import ensure_docling
    await ensure_docling()
    from docling.document_converter import DocumentConverter
    real_convert = DocumentConverter.convert
    def recording_convert(self, *args, **kwargs):
        converted_on.append(threading.get_ident())
        return real_convert(self, *args, **kwargs)
    DocumentConverter.convert = recording_convert
    doc = await processor.parse_document("plan.docx", make_docx())
    blocks = await processor.create_blocks(doc, skip_table_enrichment=True)
    print("RESULT " + json.dumps({
        "imported": bool(imported_on),
        "import_thread_is_loop": loop_thread in imported_on,
        "converted": bool(converted_on),
        "convert_thread_is_loop": loop_thread in converted_on,
        "texts": [str(b.data) for b in blocks.blocks],
    }))

asyncio.run(main())
"""


@pytest.mark.slow
@pytest.mark.real_docling
@pytest.mark.timeout(300)
def test_the_first_docx_imports_docling_off_the_loop_and_does_not_stall_it() -> None:
    """Checked by the thread the import and the conversion ran on, not by the loop's lag percentiles, which a busy
    runner stretches (``import torch`` alone holds the GIL for close to a second there)."""
    result = _run(_REAL_DOCLING, timeout=290)
    assert result["imported"], "docling was never imported: the test proves nothing"
    assert not result["import_thread_is_loop"], "docling was imported on the event loop thread"
    assert result["converted"], "the document was never converted: the test proves nothing"
    assert not result["convert_thread_is_loop"], "the document was converted on the event loop thread"
    joined = " ".join(result["texts"])
    assert "Quarterly plan" in joined and "first paragraph" in joined
