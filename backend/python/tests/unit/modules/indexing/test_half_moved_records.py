"""A PDF whose page count is not known when the run starts must keep the copy it names until the page limit has decided.

The matrix runs the real ``EventProcessor.on_event`` and ``_orchestrate_via_services`` against a stateful model of the graph, the
points (per virtual record id and collection) and the stored content (``tests/support/keep_model.py``). The oracle is the rule in
``page_limit.py``: an over-limit PDF with a searchable index and no proof of changed content stays searchable, whatever crashed
and however the retry arrives.
"""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events import events as ev
from app.modules.parsers.pdf.untrusted_pdf import InspectedPdf
from tests.support import keep_model
from tests.support.keep_model import V1, run_matrix, within_the_limit

SEEDS = (0, 1, 2)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_cell_of_the_matrix_leaves_the_record_searchable_on_the_copy_it_was_kept_on(seed: int) -> None:
    bad = {}
    cells = 0
    for key, verdict, _trace, _model in run_matrix(seed):
        cells += 1
        if verdict != "kept":
            bad[key] = verdict
    assert cells > 150
    assert not bad, f"{len(bad)} of {cells} cells lost or unsearchable: {list(bad.items())[:5]}"


def test_without_the_deferral_the_matrix_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative control: leave the old copy as soon as the new id is chosen, and records are dropped."""
    monkeypatch.setattr(ev, "_DEFER_ISOLATION", False)
    bad = [key for key, verdict, _t, _m in run_matrix(0) if verdict != "kept"]
    assert len(bad) > 20, "the matrix no longer shows the failure the deferral prevents"


@pytest.mark.parametrize("share", ["unshared", "same-collection", "other-collection"])
@pytest.mark.parametrize("label", ["pdf", "upper-ext"])
@pytest.mark.parametrize("koc", [False, True])
def test_a_file_within_the_limit_still_leaves_the_old_copy_once_the_page_limit_let_it_through(
    share: str, label: str, koc: bool
) -> None:
    record, _model = within_the_limit(share, label, koc)
    moved = record.get("virtualRecordId") != V1
    pending = bool(record.get(keep_model.PENDING_VIRTUAL_RECORD_ID))
    if share != "unshared":
        # A copy another record holds is not rewritten in place: the file is indexed under a new id (named as the pending one
        # in keep-old-copy mode, else as the record's own), which only happens once the page limit has let it through.
        assert moved or pending


def _service_event(late_result: bool, *, deferred: bool, parsed_pages: object):  # noqa: ANN202
    """Run ``_orchestrate_via_services`` with a stand-in closure and report how often it was called."""
    from tests.unit.events.test_orchestrator_flow import (
        _make_event_processor,
        _make_parse_result,
    )

    parsing_client = MagicMock()
    result = _make_parse_result()
    result.metadata = {"page_count": parsed_pages}
    parsing_client.parse = AsyncMock(return_value=result)
    sink = MagicMock()
    sink.index = AsyncMock()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=sink)
    leave = AsyncMock()
    late = AsyncMock(return_value=late_result)
    return ep, leave, late


@pytest.mark.asyncio
@pytest.mark.parametrize("parsed_pages", [500, None, "n/a"])
async def test_the_service_path_leaves_the_old_copy_exactly_once_when_the_late_check_does_not_settle(parsed_pages: object) -> None:
    ep, leave, late = _service_event(False, deferred=True, parsed_pages=parsed_pages)
    with patch.object(ep, "_persist_parsing_completed", AsyncMock()):
        events = [
            e async for e in ep._orchestrate_via_services(
                record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="a.pdf", mime_type="application/pdf",
                extension="pdf", event_type="newRecord", prev_virtual_record_id=None, file_content=b"%PDF",
                late_page_limit=late, leave_old_copy=leave,
            )
        ]
    assert events
    leave.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_service_path_never_leaves_the_old_copy_when_the_late_check_settles_the_record() -> None:
    ep, leave, late = _service_event(True, deferred=True, parsed_pages=600)
    with patch.object(ep, "_persist_parsing_completed", AsyncMock()) as persisted:
        [
            e async for e in ep._orchestrate_via_services(
                record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="a.pdf", mime_type="application/pdf",
                extension="pdf", event_type="newRecord", prev_virtual_record_id=None, file_content=b"%PDF",
                late_page_limit=late, leave_old_copy=leave,
            )
        ]
    leave.assert_not_awaited()
    persisted.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("settles", [False, True])
async def test_the_in_process_route_leaves_the_old_copy_once_or_not_at_all(settles: bool) -> None:
    from tests.unit.events.test_events import _make_event_processor

    ep, *_ = _make_event_processor()
    leave = AsyncMock()
    inspected = InspectedPdf(b"%PDF", needs_ocr=False, repaired=False, page_count=None)

    async def fake_inspect(*_a: object, **_k: object) -> InspectedPdf:
        return inspected

    async def late(_i: InspectedPdf) -> bool:
        return settles

    stop = RuntimeError("stop after the decision")

    async def run() -> None:
        async for _ in ep._dispatch_pdf_binary(
            record_name="a.pdf", record_id="rec-1", record_version=1, connector="c", org_id="org-1", pdf_binary=b"%PDF",
            virtual_record_id="v-1", after_inspection=late, leave_old_copy=leave,
        ):
            pass

    with patch("app.events.events.inspect_untrusted_pdf", side_effect=fake_inspect), patch.object(
        ep, "_write_page_count", AsyncMock(side_effect=stop)
    ):
        if settles:
            await run()
            leave.assert_not_awaited()
        else:
            with pytest.raises(RuntimeError, match="stop after"):
                await run()
            leave.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("pdfium", "settles"), [(600, True), (500, False), (None, False)])
async def test_a_service_path_with_no_parser_count_is_counted_by_pdfium_before_the_old_copy_is_left(
    pdfium: int | None, settles: bool
) -> None:
    """The parser reports no page count: pdfium counts the bytes, and over the limit the record settles with its old copy intact.
    Only when every counter fails is proceeding the final decision (the documented hole), and then the old copy is left once."""
    from app.modules.parsers.pdf.page_count import Unreadable

    ep, leave, late = _service_event(settles, deferred=True, parsed_pages=None)
    counted = AsyncMock(return_value=pdfium if pdfium is not None else Unreadable())
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new=counted), patch.object(
        ep, "_persist_parsing_completed", AsyncMock()
    ):
        [
            e async for e in ep._orchestrate_via_services(
                record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="a.pdf", mime_type="application/pdf",
                extension="pdf", event_type="newRecord", prev_virtual_record_id=None, file_content=b"%PDF",
                late_page_limit=late, leave_old_copy=leave,
            )
        ]
    counted.assert_awaited_once()
    if pdfium is None:
        late.assert_not_awaited()
        leave.assert_awaited_once()
    else:
        late.assert_awaited_once()
        assert late.await_args.args[0].page_count == pdfium
        if settles:
            leave.assert_not_awaited()
        else:
            leave.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("metadata_pages", "pdfium", "stored"), [(None, 400, 400), (350, None, 350), (None, None, None)])
async def test_the_count_the_page_limit_decided_on_is_stored_with_the_parse_status(
    metadata_pages: int | None, pdfium: int | None, stored: int | None
) -> None:
    """A real write against a record: with no count from the parser, pdfium's count is what ``pageCount`` ends as."""
    from app.config.constants.arangodb import ProgressStatus
    from app.modules.parsers.pdf.page_count import Unreadable
    from app.services.messaging.config import IndexingEvent
    from tests.unit.events.test_orchestrator_flow import (
        _make_event_processor,
        _make_parse_result,
    )

    record = {"_key": "rec-1", "orgId": "org-1", "recordName": "a.pdf", "recordType": "FILE", "contentRev": "r-1",
              "indexingStatus": ProgressStatus.IN_PROGRESS.value, "mimeType": "application/pdf"}
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=lambda *_a, **_k: dict(record))

    async def update_if_unchanged(_key: str, _coll: str, fields: dict, _expected: dict, match: dict) -> bool:
        if any(record.get(k) != v for k, v in match.items()):
            return False
        record.update({k: v for k, v in fields.items() if v is not None})
        return True

    async def update_node(_key: str, _coll: str, fields: dict, *_a: object, **_k: object) -> bool:
        record.update({k: v for k, v in fields.items() if v is not None})
        return True

    graph.update_node_if_unchanged = AsyncMock(side_effect=update_if_unchanged)
    graph.update_node = AsyncMock(side_effect=update_node)
    graph.update_node_fields_if_match = AsyncMock(return_value=True)
    parsing_client = MagicMock()
    result = _make_parse_result()
    result.metadata = {"page_count": metadata_pages} if metadata_pages is not None else {}
    parsing_client.parse = AsyncMock(return_value=result)
    ep = _make_event_processor(
        parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=MagicMock(), graph_provider=graph,
    )
    late = AsyncMock(return_value=False)
    counted = AsyncMock(return_value=pdfium if pdfium is not None else Unreadable())
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new=counted):
        stream = ep._orchestrate_via_services(
            record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="a.pdf", mime_type="application/pdf",
            extension="pdf", event_type="newRecord", prev_virtual_record_id=None, file_content=b"%PDF", content_rev="r-1",
            late_page_limit=late,
        )
        async for event in stream:
            if event.event == IndexingEvent.PARSING_COMPLETE:
                break
        await stream.aclose()
    assert record.get("pageCount") == stored
    assert record["parsingStatus"] == ProgressStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.parametrize("resumes", [False, True])
async def test_the_old_copy_is_left_late_for_a_fresh_pdf_and_at_once_on_a_resume(resumes: bool) -> None:
    """A resume has no parse for the page limit to wait on: it moves the record onto the copy its points are under at once."""
    from app.config.constants.arangodb import MimeTypes
    from tests.unit.events.test_events import _drain
    from tests.unit.events.test_events_resume import VRID, _setup

    old_vrid = "vr-old"
    ep, processor, gp, event = _setup(
        {"virtualRecordId": old_vrid, "mimeType": MimeTypes.PDF.value, keep_model.PENDING_VIRTUAL_RECORD_ID: VRID},
        mime=MimeTypes.PDF.value, event_type="reindexRecord",
    )
    event["payload"]["virtualRecordId"] = old_vrid
    if not resumes:
        gp.get_document.return_value.update({"embedDeliveryId": "another-delivery"})
    gate = ev.PageLimitGate(late_check=AsyncMock(return_value=False))
    processor.process_pdf_document = MagicMock(side_effect=lambda *a, **k: _empty_gen())
    writes = AsyncMock(return_value=True)
    with patch.object(ep, "_gate_page_limit", AsyncMock(return_value=gate)), patch.object(ep, "update_record_fields", writes), patch(
        "app.events.events.inspect_untrusted_pdf", AsyncMock(return_value=InspectedPdf(b"%PDF", needs_ocr=False, repaired=False, page_count=3))
    ):
        # Only the record writes before the parse are of interest: what follows may fail on the stand-in processor.
        with contextlib.suppress(Exception):
            await _drain(ep.on_event(event))
    moved = any(call.args[1].get("virtualRecordId") for call in writes.await_args_list)
    assert moved is resumes
    assert processor.resume_from_checkpoint.called is resumes


async def _empty_gen():  # noqa: ANN202
    return
    yield


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "applied"),
    [
        ({"contentRev": "r-1", "virtualRecordId": "v-old"}, True),
        ({"contentRev": "r-1", "virtualRecordId": "v-moved"}, False),
        ({"contentRev": "r-2", "virtualRecordId": "v-old"}, False),
    ],
)
async def test_a_kept_record_is_put_back_only_while_it_is_still_on_the_copy_and_revision_the_run_started_with(
    current: dict, applied: bool
) -> None:
    from tests.unit.events.test_events import _make_event_processor

    ep, _logger, _processor, gp = _make_event_processor()
    gp.get_document = AsyncMock(return_value=dict(current))
    gp.update_node_fields_if_match = AsyncMock(return_value=True)
    record = {"virtualRecordId": "v-old", "contentRev": "r-1", "indexingStatus": "COMPLETED", "parsingStatus": "COMPLETED"}
    with patch.object(ev, "apply_keep", AsyncMock()):
        owned = await ep._put_back_kept_record(
            record=record, doc=dict(record), record_id="rec-1", content_rev="r-1", org_id="org-1", keep=MagicMock(),
            md5=None, force_reindex=False,
        )
    assert owned is applied
    assert gp.update_node_fields_if_match.await_count == (1 if applied else 0)
