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
# The service: the functions that gained a lane branch hold legacy behaviour too, so they are
# pinned by the behaviour traces below rather than by hash.
SERVICE_MODIFIED = {"parse_pdf_endpoint", "_parse_holding_gate"}
SERVICE_ADDED: set[str] = set()  # only imports are added


def test_off_pin_covers_whole_module_except_allowlist() -> None:
    modified, added = changed(PROCESSOR)
    assert imports(modified) <= {"import:from concurrent.futures import ProcessPoolExecutor"}  # merged with Future
    assert modified - imports(modified) <= PROCESSOR_MODIFIED, sorted(modified)
    assert top_names(added) <= PROCESSOR_ADDED, sorted(top_names(added) - PROCESSOR_ADDED)
    modified, added = changed(SERVICE)
    assert imports(modified) <= {"import:from app.modules.parsers.pdf.docling_processor import DoclingProcessor"}
    assert modified - imports(modified) <= SERVICE_MODIFIED, sorted(modified)
    assert top_names(added) == SERVICE_ADDED


def test_off_pin_allowlist_is_not_padded() -> None:
    modified, added = changed(PROCESSOR)
    assert modified - imports(modified) == PROCESSOR_MODIFIED
    assert top_names(added) == PROCESSOR_ADDED
    modified, _ = changed(SERVICE)
    assert modified - imports(modified) == SERVICE_MODIFIED


def test_off_pin_parse_document_gained_only_the_lane_branch() -> None:
    now = body_without_lane_branch(PROCESSOR, "DoclingProcessor.parse_document")
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
