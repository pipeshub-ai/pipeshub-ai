"""The legacy dispatch reads a file its labels cannot route with the reader the gate accepted it for (F23b)."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import file_types
from app.config.file_types import Dispatch, Route
from app.events.events import DedupDecision, EventProcessor
from app.modules.parsers.code_parser.routing import CodeFileRoute
from app.services.parsing.interface import UnsupportedFormatError
from tests.unit.config.file_types import gen_pinned_decisions as gen
from tests.unit.config.file_types.test_registry_invariants import _ROUTE_HANDLER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_CODE = Dispatch(Route.CODE, None)


async def _dispatch(
    ext: str | None, mime: str | None, name: str, record_type: str, gate_route: Dispatch | None,
    *, service: bool = False,
) -> tuple[MagicMock, EventProcessor]:
    """``on_event`` with every parser stubbed; the processor records which one it was handed."""
    processor = gen._processor()
    gp = AsyncMock()
    gp.update_node = AsyncMock(return_value=True)
    gp.get_document.return_value = {"_key": "r1", "recordType": record_type, "recordName": name}
    ep = EventProcessor(gen._Silent(), processor, gp, MagicMock())
    ep.parsing_client = MagicMock(circuit_open=False)

    async def pdf(*_a: object, **_k: object) -> AsyncIterator[object]:
        processor.routed.append("_dispatch_pdf_binary")
        return
        yield

    data = {
        "eventType": "newRecord",
        "gateRoute": gate_route,
        "payload": {
            "recordId": "r1", "orgId": "org-1", "virtualRecordId": None, "version": 1, "connectorName": "",
            "extension": ext, "mimeType": mime, "recordName": name, "buffer": b"body { color: red }",
        },
    }
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_dispatch_pdf_binary", side_effect=pdf), \
         patch.object(ep, "_use_service_pipeline", return_value=service):
        async for _ in ep.on_event(data):
            pass
    return processor, ep


def _route_case(route: Route) -> Dispatch:
    """The dispatch the registry pairs with *route*: its first type, or none for code."""
    if route is Route.CODE:
        return _CODE
    return Dispatch(route, next(ft for ft in file_types.FILE_TYPES if ft.route is route))


@pytest.mark.parametrize("route", list(Route), ids=lambda r: r.value)
async def test_every_route_has_a_branch(route: Route) -> None:
    # Labels no branch matches: only the handed route can reach a parser.
    processor, _ = await _dispatch(None, "application/x-f23b-unroutable", "file", "FILE", _route_case(route))
    assert processor.routed[:1] == [_ROUTE_HANDLER[route]]


@pytest.mark.parametrize(("ext", "mime"), [("css", "text/css"), ("tf", "application/octet-stream")])
async def test_a_repository_text_file_the_labels_cannot_route_is_read_as_code(ext: str, mime: str) -> None:
    name = f"style.{ext}"
    processor, _ = await _dispatch(ext, mime, name, "CODE_FILE", _CODE)
    assert processor.routed == ["process_code_document"]
    assert processor.process_code_document.call_args.kwargs["extension"] == ext

    # The service path plans the code file first: only a record read as a code file gets there.
    plan = MagicMock(route=CodeFileRoute.SKIP)
    with patch("app.events.events.plan_code_file", return_value=plan) as planner:
        processor, _ = await _dispatch(ext, mime, name, "CODE_FILE", _CODE, service=True)
    planner.assert_called_once()
    assert planner.call_args.kwargs["repository_file"] is True
    assert processor.routed == ["process_code_document"]


async def test_without_the_gate_route_the_same_file_is_refused() -> None:
    with pytest.raises(UnsupportedFormatError):
        await _dispatch("css", "text/css", "style.css", "CODE_FILE", None)


async def test_the_records_own_route_wins_over_the_gate_route() -> None:
    processor, _ = await _dispatch("md", "text/markdown", "README.md", "CODE_FILE", _CODE)
    assert processor.routed == ["process_md_document"]


def test_the_read_plan_takes_the_gate_route_only_where_the_labels_name_none() -> None:
    own = EventProcessor._read_plan("CODE_FILE", "text/css", "css", None, "style.css", None)
    assert own == (None, "css", False)
    handed = EventProcessor._read_plan("CODE_FILE", "text/css", "css", None, "style.css", _CODE)
    assert handed == (_CODE, "css", True)
    # A type the extension decides is read as itself, not as source, even from a repository.
    assert EventProcessor._read_plan("CODE_FILE", "text/plain", "log", None, "a.log", None)[2] is False
    # The code extension still comes from the path, then the name, when the record has none.
    assert EventProcessor._read_plan("CODE_FILE", "text/plain", None, "src/a.py", "a", None)[1] == "py"
    assert EventProcessor._read_plan("CODE_FILE", "text/plain", "unknown", None, "dir/b.rs", None)[1] == "rs"


@pytest.mark.parametrize(("ext", "mime", "name", "record_type", "parser"), [
    ("unknown", None, "Report.docx", "FILE", "process_docx_document"),
    ("unknown", "application/octet-stream", "Report.xlsx", "FILE", "process_excel_document"),
    (None, "application/octet-stream", "report.pdf", "CODE_FILE", "_dispatch_pdf_binary"),
])
async def test_a_file_accepted_by_its_name_is_read_by_it(
    ext: str | None, mime: str | None, name: str, record_type: str, parser: str,
) -> None:
    processor, _ = await _dispatch(ext, mime, name, record_type, None)
    assert processor.routed[:1] == [parser]


@pytest.mark.parametrize(("mime", "name"), [("application/zip", "Report.docx"), ("video/mpeg", "Report.pdf")])
async def test_a_specific_mime_is_not_read_by_the_name(mime: str, name: str) -> None:
    with pytest.raises(UnsupportedFormatError):
        await _dispatch("unknown", mime, name, "FILE", None)


def test_the_read_plan_reads_the_name_where_a_path_is_not_a_string() -> None:
    assert EventProcessor._read_plan("FILE", "application/octet-stream", None, ["x"], "a.pdf", None)[1] == "pdf"
