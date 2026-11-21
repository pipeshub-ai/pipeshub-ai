"""A PDF whose page count is not known when the run starts must keep the copy it names until the page limit has decided.

The matrix runs the real ``EventProcessor.on_event`` and ``_orchestrate_via_services`` against a stateful model of the graph, the
points (per virtual record id and collection) and the stored content (``tests/support/keep_model.py``). The oracle is the rule in
``page_limit.py``: an over-limit PDF with a searchable index and no proof of changed content stays searchable, whatever crashed
and however the retry arrives.
"""
from __future__ import annotations

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
