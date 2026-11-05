"""``docling`` is imported when a document needs it, not when a service starts.

The first test blocks the library entirely and runs the indexing path for PDFs and the
parser set; the second runs the real library in a fresh interpreter and checks that the
import happens off the event loop and does not stall it (scenarios T3, T5, T11).
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
import asyncio, importlib.abc, json, logging, threading, time
from io import BytesIO
from unittest.mock import MagicMock

loop_thread = threading.get_ident()
imported_on = []
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
    lags = []
    async def probe():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            lags.append(now - last - 0.05)
            last = now
    task = asyncio.create_task(probe())
    await asyncio.sleep(0.2)
    lags.clear()  # measure from the first use of docling, not from start-up
    processor = DoclingProcessor(logging.getLogger("t"), MagicMock())
    started = time.perf_counter()
    doc = await processor.parse_document("plan.docx", make_docx())
    elapsed = time.perf_counter() - started
    blocks = await processor.create_blocks(doc, skip_table_enrichment=True)
    task.cancel()
    lags.sort()
    p99 = lags[int(len(lags) * 0.99) - 1] if len(lags) > 1 else 0.0
    p95 = lags[int(len(lags) * 0.95) - 1] if len(lags) > 1 else 0.0
    print("RESULT " + json.dumps({
        "p99_lag": p99, "p95_lag": p95, "max_lag": lags[-1], "worst": lags[-5:], "ticks": len(lags), "seconds": elapsed,
        "import_thread_is_loop": bool(imported_on) and imported_on[0] == loop_thread,
        "imported": bool(imported_on),
        "texts": [str(b.data) for b in blocks.blocks],
    }))

asyncio.run(main())
"""


@pytest.mark.slow
@pytest.mark.real_docling
@pytest.mark.timeout(300)
def test_the_first_docx_imports_docling_off_the_loop_and_does_not_stall_it() -> None:
    result = _run(_REAL_DOCLING, timeout=290)
    assert result["imported"], "docling was never imported: the test proves nothing"
    assert not result["import_thread_is_loop"], "docling was imported on the event loop thread"
    assert result["ticks"] >= 10, "the probe task barely ran while docling was imported"
    # `import torch` initialises native code while holding the GIL (about 0.4 s on a warm
    # cache), so a handful of ticks are late and the p99 of a short import sits on that
    # stall. What the guard promises is that the loop is not blocked for the import's
    # duration (10 to 30 s inline): the bulk stays responsive and no stall nears a second.
    detail = f"p95 {result['p95_lag']:.3f}s p99 {result['p99_lag']:.3f}s worst {result['worst']}"
    assert result["p95_lag"] < 0.25, f"event loop lag during the first import: {detail}"
    assert result["max_lag"] < 1.0, f"event loop stalled during the first import: {detail}"
    joined = " ".join(result["texts"])
    assert "Quarterly plan" in joined and "first paragraph" in joined
