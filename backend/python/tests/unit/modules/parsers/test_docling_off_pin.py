"""``PARSE_LANES`` off leaves local Docling and the Docling service exactly as they were (T75).

The recorded trace holds, from main: the order of executor creation and submission, the answers of
the worker-count settings (``auto`` reads as 1), and the status code, body and permit calls of the
endpoint and of ``_parse_holding_gate`` for success, a failed parse, a failed serialization,
backpressure and a caller timeout.
"""
from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest

from tests.unit.modules.parsers.docling_raster_trace import (
    record_docling,
    record_service,
)
from tests.unit.modules.parsers.off_pin_support import (
    ROOT,
    TRACE,
    body_without_lane_branch,
    changed,
    current_hashes,
    imports,
    plain,
    top_names,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

PROCESSOR = "docling_processor.py"
SERVICE = "docling_service.py"
PROCESSOR_MODIFIED = {"DoclingProcessor.parse_document"}  # a first statement; the rest is pinned below
PROCESSOR_ADDED = {
    "AUTO_IDLE_SECONDS", "AUTO_IDLE_TICK_SECONDS", "_lease_wait", "_lease_cancelled", "_leases", "_auto_lock", "_auto",
    "lease_scope", "lanes_active", "conversion_weight", "_lane_mode", "_Leases", "_Held",
    "_DoclingExecutor", "_get_auto_executor", "shutdown_docling_pool", "docling_lane_stats", "_parse_on_lanes",
    "_convert_in_thread", "_convert_in_executor",
}
# What deferring the ``docling`` import changed in this module (the processor imports nothing from
# ``docling`` at load). It is not part of the lane work, so it is listed apart from it:
#  - the docling import lines are gone or moved into the functions that use them;
#  - ``parse_document`` awaits ``ensure_docling()`` and calls the converter through a helper that
#    runs in the worker thread: the off-path test below reverses exactly those edits and compares
#    the body with main's;
#  - the other entries are re-pinned to the hash they have now. Each differs from main only by
#    the moved imports (``_get_converter``, ``_parse_document_in_worker``), by the lazily built
#    converter (``__init__`` no longer builds it) or by the type-only import block. The traces
#    below still replay the behaviour of all of them.
LAZY_IMPORT_IMPORTS = {
    "import:from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend",
    "import:from docling.datamodel.base_models import DocumentStream, InputFormat",
    "import:from docling.datamodel.pipeline_options import TableFormerMode, TableStructureOptions, ThreadedPdfPipelineOptions",
    "import:from docling.document_converter import DocumentConverter, MarkdownFormatOption, PdfFormatOption, WordFormatOption",
}
LAZY_IMPORT_REPINNED = {
    "DoclingProcessor.__init__": "2530d7f496cd4485",
    "if:TYPE_CHECKING": "1331c17b12c0a195",
    "_get_converter": "4dadac7cb530286a",
    "_parse_document_in_worker": "c506f30760b63571",
    "assign:DOCLING_ROUTED_FORMATS": None,  # removed: ``docling_routed_formats()`` returns the same tuple
}
LAZY_IMPORT_ADDED = {
    "DoclingProcessor._converter", "DoclingProcessor.converter", "DoclingProcessor._convert_in_process",
    "docling_routed_formats",
}
# The service: the functions that gained a lane branch hold legacy behaviour too, so they are
# pinned by the behaviour traces below rather than by hash.
SERVICE_MODIFIED = {"parse_pdf_endpoint", "_parse_holding_gate"}
# ``initialize`` gained one statement, ``await ensure_docling()`` (the service loads Docling at start
# instead of on the first request): re-pinned to its hash now. It runs before the endpoint takes
# requests, so the endpoint traces below are unaffected.
SERVICE_INITIALIZE = ("DoclingService.initialize", "2f855ea0c01f8bba")
SERVICE_ADDED: set[str] = set()  # only imports are added


def _lazy_import_split(modified: set[str]) -> tuple[set[str], set[str]]:
    """``(the lane work's modified entries, the entries the lazy import changed)``."""
    lazy = (modified & set(LAZY_IMPORT_REPINNED)) | (modified & LAZY_IMPORT_IMPORTS)
    return modified - lazy, lazy


def test_off_pin_covers_whole_module_except_allowlist() -> None:
    modified, added = changed(PROCESSOR)
    modified, lazy = _lazy_import_split(modified)
    assert imports(modified) <= {"import:from concurrent.futures import ProcessPoolExecutor"}  # merged with Future
    assert modified - imports(modified) <= PROCESSOR_MODIFIED, sorted(modified)
    assert top_names(added - LAZY_IMPORT_ADDED) <= PROCESSOR_ADDED, sorted(top_names(added - LAZY_IMPORT_ADDED) - PROCESSOR_ADDED)
    modified, added = changed(SERVICE)
    assert imports(modified) <= {"import:from app.modules.parsers.pdf.docling_processor import DoclingProcessor"}
    assert modified - imports(modified) - {SERVICE_INITIALIZE[0]} <= SERVICE_MODIFIED, sorted(modified)
    assert top_names(added) == SERVICE_ADDED


def test_off_pin_allowlist_is_not_padded() -> None:
    modified, added = changed(PROCESSOR)
    modified, lazy = _lazy_import_split(modified)
    assert modified - imports(modified) == PROCESSOR_MODIFIED
    assert lazy == set(LAZY_IMPORT_REPINNED) | LAZY_IMPORT_IMPORTS
    assert added & LAZY_IMPORT_ADDED == LAZY_IMPORT_ADDED
    assert top_names(added - LAZY_IMPORT_ADDED) == PROCESSOR_ADDED
    modified, _ = changed(SERVICE)
    assert modified - imports(modified) == SERVICE_MODIFIED | {SERVICE_INITIALIZE[0]}
    assert current_hashes(SERVICE)[SERVICE_INITIALIZE[0]] == SERVICE_INITIALIZE[1]


def test_off_pin_lazy_import_entries_stay_where_they_were_re_pinned() -> None:
    """A later edit to one of these functions must come with a new look at the off path."""
    now = current_hashes(PROCESSOR)
    for key, digest in LAZY_IMPORT_REPINNED.items():
        assert now.get(key) == digest, key


def test_off_pin_parse_document_gained_only_the_lane_branch_and_the_lazy_import() -> None:
    now = body_without_lane_branch(PROCESSOR, "DoclingProcessor.parse_document", undo_lazy_import=True)
    assert now == TRACE["bodies"]["DoclingProcessor.parse_document"]


@pytest.fixture(params=["unset", "off", "empty", "junk"])
def lanes_off(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.param == "unset":
        monkeypatch.delenv("PARSE_LANES", raising=False)
    else:
        monkeypatch.setenv("PARSE_LANES", {"off": "off", "empty": "", "junk": "yes"}[request.param])
    yield


def test_off_replays_the_recorded_docling_trace(lanes_off: None) -> None:
    assert plain(record_docling()) == TRACE["docling"]


def test_off_trace_docling_endpoint(lanes_off: None) -> None:
    now = plain(record_service())
    for case in ("success", "parse_error", "serialization_error", "backpressure", "timeout"):
        assert now[case] == TRACE["service"][case], case


def test_off_trace_parse_holding_gate(lanes_off: None) -> None:
    assert plain(record_service())["holding_gate"] == TRACE["service"]["holding_gate"]


def test_off_abandon_event_does_not_stop_a_running_task(lanes_off: None) -> None:
    """In off the endpoint still sets an abandon event, and nothing reads it."""
    import asyncio
    from unittest.mock import MagicMock

    import app.services.docling.docling_service as mod

    released: list[int] = []
    finish = asyncio.Event()

    async def slow(*a: object, **k: object) -> MagicMock:
        await finish.wait()
        return MagicMock(model_dump_json=lambda: "{}")

    async def run() -> str:
        service = MagicMock()
        service.parse_pdf_only = slow
        abandon = asyncio.Event()
        abandon.set()  # already abandoned
        from unittest.mock import patch

        with patch.object(mod, "_release_docling_gate", released.append):
            task = asyncio.ensure_future(mod._parse_holding_gate(service, "a.pdf", b"x", None, 4, abandon))
            await asyncio.sleep(0.05)
            finish.set()
            return await task

    assert asyncio.run(run()) == "{}"
    assert released == [4]


def test_off_auto_reads_as_one() -> None:
    assert TRACE["docling"]["grid"]["docling/'auto'"] == 1
    assert TRACE["docling"]["grid"]["docling/'AUTO'"] == 1


def test_the_service_module_still_calls_the_processor_the_same_way() -> None:
    tree = ast.parse((ROOT / "app/services/docling/docling_service.py").read_text())
    calls = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("parse_document")]
    assert calls == ["self.processor.parse_document(record_name, pdf_binary, page_range=page_range)"]
