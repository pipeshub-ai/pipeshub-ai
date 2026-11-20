"""Page-limit behaviours found in review: where a keep is decided, which writes it makes, and what counts as an index."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.events.events import EventProcessor
from app.modules.indexing import page_limit
from app.modules.parsers.pdf.page_count import Unavailable, Unreadable
from app.modules.parsers.pdf.untrusted_pdf import InspectedPdf
from app.services.messaging.config import IndexingEvent
from app.utils.user_errors import PDF_PAGE_LIMIT_NOTE
from tests.unit.modules.indexing.test_page_limit import (
    COMPLETED,
    MD5,
    REV,
    VRID,
    World,
    searchable,
)

UPDATE = "updateRecord"


# --- the event's org id -----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_event_without_an_org_id_refuses_under_the_stored_records_org() -> None:
    """The refusal's clean-up must look in the record's org, as the rest of the event does."""
    from app.core.org_context import for_org

    world = World({"indexingStatus": "QUEUED"}, extra_payload={"orgId": None})
    world.count(600)
    with for_org("org-1", job="test"):
        await world.run()
    assert world.refused
    assert world.unindex.await_args.args[1] == "org-1"


# --- a keep is decided before the record or its virtual record id is written ---------------------------------------------


async def _shared_vrid_update(count: object, pages: int) -> World:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(count)
    world.inspected(pages)
    # Another record holds the same virtual record id, so an update would isolate this one under a new id.
    world.gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"id": "rec-1"}, {"id": "rec-2"}])
    world.rewrite = world.stack.enter_context(patch.object(world.ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock))
    await world.run(event_type=UPDATE)
    return world


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [Unreadable(), Unavailable()])
async def test_a_keep_after_a_count_outage_leaves_the_virtual_record_id_and_its_vectors_alone(count: object) -> None:
    world = await _shared_vrid_update(count, 600)
    assert not world.indexed and not world.refused
    assert world.graph.record["virtualRecordId"] == VRID, "the record was moved off the index it was kept for"
    world.rewrite.assert_not_awaited()
    assert world.graph.record["indexingStatus"] == COMPLETED
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [Unreadable(), Unavailable()])
async def test_a_file_within_the_limit_after_a_count_outage_still_takes_the_normal_update(count: object) -> None:
    world = await _shared_vrid_update(count, 20)
    assert world.indexed
    assert world.graph.record["virtualRecordId"] != VRID
    world.rewrite.assert_awaited_once_with(VRID)


@pytest.mark.asyncio
async def test_the_inspection_taken_for_the_decision_is_the_one_that_parses() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(Unreadable())
    inspect = world.inspected(20)
    await world.run()
    inspect.assert_awaited_once()


# --- a keep never overwrites a revision that was queued meanwhile ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", ["QUEUED", "NOT_STARTED"])
async def test_a_revision_queued_while_the_keep_was_deciding_is_not_marked_completed(reset: str) -> None:
    world = World(searchable(indexingStatus=reset))
    world.graph.hook_on_every_write = True
    fired: list[bool] = []

    def new_revision(graph: object) -> None:
        if not fired:
            fired.append(True)
            graph.record.update(contentRev="r2-queued")

    world.graph.cas_hook = new_revision
    world.count(600)
    await world.run()
    assert fired
    assert world.graph.record["contentRev"] == "r2-queued"
    assert world.graph.record["indexingStatus"] == reset, "a revision that was never indexed was marked COMPLETED"
    assert "indexingNote" not in world.graph.record


@pytest.mark.asyncio
async def test_a_status_restore_and_its_note_are_one_conditional_write() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    seen: list[tuple[dict, dict]] = []
    original = world.graph.update_node_fields_if_match

    async def spy(key: str, collection: str, updates: dict, expected: dict, *a: object, **kw: object) -> bool:
        seen.append((dict(updates), dict(expected)))
        return await original(key, collection, updates, expected, *a, **kw)

    world.gp.update_node_fields_if_match = spy
    world.count(600)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    updates, expected = seen[0]
    assert updates["indexingStatus"] == COMPLETED and updates["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert set(expected) == {"indexingStatus", "contentRev", "virtualRecordId"}
    assert len(seen) == 1


# --- stored blocks alone are not an index -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_record_with_stored_blocks_but_no_vectors_is_refused_not_marked_indexed() -> None:
    """A store-only run leaves blocks and no vectors: keeping it would set the indexed flag over nothing."""
    record = {"indexingStatus": "AUTO_INDEX_OFF", "virtualRecordId": VRID}
    world = World(record)
    world.processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value=set())
    world.count(600)
    await world.run()
    assert world.refused
    assert world.graph.record.get("hasIndexedCopy") is not True


@pytest.mark.asyncio
async def test_a_record_with_stored_blocks_and_vectors_is_kept_and_flagged() -> None:
    world = World({"indexingStatus": "QUEUED", "virtualRecordId": VRID})
    world.count(600)
    await world.run()
    assert not world.refused
    assert world.graph.record["hasIndexedCopy"] is True


@pytest.mark.asyncio
async def test_a_lookup_that_cannot_answer_keeps_the_index() -> None:
    world = World({"indexingStatus": "QUEUED", "virtualRecordId": VRID})
    world.processor.indexing_pipeline.orgs_holding_points = AsyncMock(side_effect=RuntimeError("vector store down"))
    world.count(600)
    await world.run()
    assert not world.refused


# --- the parsing service path -----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_service_pipeline_applies_the_page_limit_to_the_parsers_page_count() -> None:
    from tests.unit.events.test_orchestrator_flow import (
        _make_event_processor,
        _make_parse_result,
    )

    parsing_client = MagicMock()
    result = _make_parse_result()
    result.metadata = {"page_count": 600}
    parsing_client.parse = AsyncMock(return_value=result)
    sink = MagicMock()
    sink.index = AsyncMock()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=sink)
    persisted = AsyncMock()
    seen: list[int | None] = []

    async def refuse(inspected: InspectedPdf) -> bool:
        seen.append(inspected.page_count)
        return True

    with patch.object(ep, "_persist_parsing_completed", persisted):
        events = [
            e async for e in ep._orchestrate_via_services(
                record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="a.pdf",
                mime_type="application/pdf", extension="pdf", event_type="newRecord", prev_virtual_record_id=None,
                file_content=b"%PDF", late_page_limit=refuse,
            )
        ]
    assert seen == [600]
    assert [e.event for e in events] == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    persisted.assert_not_awaited()
    sink.index.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_service_pipeline_goes_on_when_the_parsers_count_is_within_the_limit() -> None:
    from tests.unit.events.test_orchestrator_flow import (
        _make_event_processor,
        _make_parse_result,
    )

    parsing_client = MagicMock()
    result = _make_parse_result()
    result.metadata = {"page_count": 500}
    parsing_client.parse = AsyncMock(return_value=result)
    sink = MagicMock()
    sink.index = AsyncMock()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=sink)
    keep_going = AsyncMock(return_value=False)
    with patch.object(ep, "_persist_parsing_completed", AsyncMock()):
        events = [
            e async for e in ep._orchestrate_via_services(
                record_id="rec-1", org_id="org-1", virtual_record_id="v-1", record_name="a.pdf",
                mime_type="application/pdf", extension="pdf", event_type="newRecord", prev_virtual_record_id=None,
                file_content=b"%PDF", late_page_limit=keep_going,
            )
        ]
    keep_going.assert_awaited_once()
    sink.index.assert_awaited_once()
    assert events[-1].event == IndexingEvent.INDEXING_COMPLETE


@pytest.mark.asyncio
async def test_the_event_hands_the_service_pipeline_the_late_check_when_the_count_is_unknown() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unavailable())
    world.ep.parsing_client = MagicMock(circuit_open=False)
    captured: dict = {}

    async def fake(**kwargs: object):  # noqa: ANN202
        captured.update(kwargs)
        return
        yield

    world.stack.enter_context(patch.object(world.ep, "_use_service_pipeline", return_value=True))
    world.stack.enter_context(patch.object(world.ep, "_orchestrate_via_services", side_effect=fake))
    await world.run()
    assert callable(captured.get("late_page_limit"))


# --- this release accepts indexedMd5 and pendingTaxonomyRefs but writes neither --------------------------------------------


APP = Path(page_limit.__file__).resolve().parents[2]
ACCEPT_ONLY = ("indexedMd5", "pendingTaxonomyRefs")
ALLOWED = {
    "schema/arango/documents.py",  # the record schema accepts them
    "services/graph_db/common/utils.py",  # and the record endpoint never returns them
    "modules/indexing/page_limit.py",  # a reader: absent reads as null
}


def test_no_application_code_writes_the_accept_only_fields() -> None:
    """A mixed-version fleet: a process of the previous release rejects a record that carries a key it does not know."""
    offenders = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in ACCEPT_ONLY) and relative not in ALLOWED:
            offenders.append(relative)
    assert not offenders, f"these files name a field this release must only accept: {offenders}"


def test_the_one_reader_never_writes_the_indexed_md5() -> None:
    source = (APP / "modules/indexing/page_limit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            assert "indexedMd5" not in [getattr(k, "value", None) for k in node.keys]
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            assert "indexedMd5" not in ast.unparse(node)
    assert not re.search(r"indexedMd5\W*:", source.replace('"""', "")), "a mapping entry for indexedMd5"


def test_the_event_processor_no_longer_remembers_an_indexed_md5() -> None:
    assert not hasattr(EventProcessor, "indexed_md5")
    with pytest.raises(ImportError):
        __import__("app.modules.indexing.indexed_bytes")
    assert ProgressStatus.COMPLETED.value == COMPLETED


# --- the parsing-service path decides from the parser's count, not from a gate inspection ----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [Unreadable(), Unavailable()])
async def test_the_service_path_takes_no_gate_inspection_and_gets_the_late_check(count: object) -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(count)
    inspect = world.inspected(600)
    world.ep.parsing_client = MagicMock(circuit_open=False)
    captured: dict = {}

    async def fake(**kwargs: object):  # noqa: ANN202
        captured.update(kwargs)
        return
        yield

    world.stack.enter_context(patch.object(world.ep, "_use_service_pipeline", return_value=True))
    world.stack.enter_context(patch.object(world.ep, "_orchestrate_via_services", side_effect=fake))
    await world.run(UPDATE)
    inspect.assert_not_awaited()
    assert callable(captured.get("late_page_limit"))


@pytest.mark.asyncio
async def test_the_late_keep_on_the_service_path_restores_only_the_revision_this_run_wrote() -> None:
    world = World(searchable(indexingStatus="QUEUED", contentRev="r-old"))
    world.count(Unavailable())
    world.ep.parsing_client = MagicMock(circuit_open=False)
    late: dict = {}

    async def fake(**kwargs: object):  # noqa: ANN202
        late["check"] = kwargs["late_page_limit"]
        return
        yield

    world.stack.enter_context(patch.object(world.ep, "_use_service_pipeline", return_value=True))
    world.stack.enter_context(patch.object(world.ep, "_orchestrate_via_services", side_effect=fake))
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    # The run marked the record IN_PROGRESS under its own revision; the parser then reports 600 pages.
    world.graph.record.update(indexingStatus="IN_PROGRESS", contentRev=REV)
    assert await late["check"](InspectedPdf(content=b"", needs_ocr=False, repaired=False, page_count=600)) is True
    assert world.graph.record["indexingStatus"] == COMPLETED and world.graph.record["contentRev"] == "r-old"
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE and not world.refused

    # A revision that landed meanwhile is never overwritten.
    world.graph.record.update(indexingStatus="QUEUED", contentRev="r-newer")
    assert await late["check"](InspectedPdf(content=b"", needs_ocr=False, repaired=False, page_count=600)) is True
    assert world.graph.record["contentRev"] == "r-newer" and world.graph.record["indexingStatus"] == "QUEUED"


# --- a file that grew past the limit is a changed file ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "pages"), [(300, 501), (300, 600), (500, 501), (1, 501)])
async def test_a_stored_count_within_the_limit_and_a_fresh_count_over_it_is_refused(stored: int, pages: int) -> None:
    """The sync rewrote md5Checksum to the new file's, so only the count says the bytes changed."""
    world = World(searchable(indexingStatus="QUEUED", contentRev="r1-indexed", md5Checksum=MD5, pageCount=stored))
    world.count(pages)
    await world.run(UPDATE)
    assert world.refused
    world.unindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_stored_count_over_the_limit_or_none_still_keeps_the_index() -> None:
    for stored in ({"pageCount": 700}, {}):
        world = World(searchable(indexingStatus="QUEUED", **stored))
        world.count(600)
        await world.run(UPDATE)
        assert not world.refused
        assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE


@pytest.mark.asyncio
async def test_growth_past_the_limit_found_by_the_inspection_is_refused() -> None:
    world = World(searchable(indexingStatus="QUEUED", pageCount=300))
    world.count(Unreadable())
    world.inspected(600)
    await world.run(UPDATE)
    assert world.refused


# --- an inspection with no page count leaves the late check in place -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_inspection_without_a_page_count_does_not_count_the_file() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(Unreadable())
    world.inspected(None)
    gate_args: dict = {}
    original = world.ep._check_duplicate_by_md5

    async def spy(*a: object, **kw: object):  # noqa: ANN202
        gate_args.update(kw)
        return await original(*a, **kw)

    world.stack.enter_context(patch.object(world.ep, "_check_duplicate_by_md5", side_effect=spy))
    await world.run(UPDATE)
    assert gate_args.get("skip_over_limit_twins") is True


@pytest.mark.asyncio
async def test_a_no_pool_process_does_not_count_the_outage_metric() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unavailable(no_pool=True))
    with patch.object(page_limit.PAGE_COUNT_UNAVAILABLE, "inc") as inc:
        await world.run()
    inc.assert_not_called()


# --- the service-path keep on a shared virtual record id; the org fails closed --------------------------------------------------


def _service_world(record: dict, *, shared: bool) -> tuple[World, dict]:
    world = World(record)
    world.count(Unavailable())
    world.ep.parsing_client = MagicMock(circuit_open=False)
    if shared:
        world.gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"id": "rec-1"}, {"id": "rec-2"}])
    world.rewrite = world.stack.enter_context(
        patch.object(world.ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock)
    )
    world.cleanup = world.stack.enter_context(patch.object(world.ep, "_spawn_abandoned_vrid_cleanup"))
    seen: dict = {}

    async def fake(**kwargs: object):  # noqa: ANN202 - stands in for the parsing service: it reports 600 pages
        seen["vrid_at_parse"] = world.graph.record.get("virtualRecordId")
        await kwargs["late_page_limit"](InspectedPdf(content=b"", needs_ocr=False, repaired=False, page_count=600))
        return
        yield

    world.stack.enter_context(patch.object(world.ep, "_use_service_pipeline", return_value=True))
    world.stack.enter_context(patch.object(world.ep, "_orchestrate_via_services", side_effect=fake))
    return world, seen


@pytest.mark.asyncio
async def test_a_service_keep_after_the_run_left_the_shared_virtual_record_id_goes_back_to_it() -> None:
    """The run isolated the record under a new id and took it out of the old id's points: it is put back on the old id, whose
    membership is rewritten with the record holding it again, and the id the run minted is released."""
    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=True)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    record = world.graph.record
    assert seen["vrid_at_parse"] != VRID
    assert not world.refused
    assert record["virtualRecordId"] == VRID and record["indexingStatus"] == COMPLETED
    assert record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    world.processor.indexing_pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with(VRID)
    assert [c.args for c in world.rewrite.await_args_list][-1] == (seen["vrid_at_parse"],)
    assert world.cleanup.call_args.args[1] == seen["vrid_at_parse"]
    world.unindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_service_keep_that_cannot_rewrite_the_old_membership_fails_the_event_with_the_record_back_on_its_id() -> None:
    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=True)
    world.processor.indexing_pipeline.rewrite_or_delete_vector_membership = AsyncMock(side_effect=RuntimeError("store down"))
    with patch("app.events.events.content_revision", return_value=REV), pytest.raises(RuntimeError):
        await world.run(UPDATE)
    assert world.graph.record["virtualRecordId"] == VRID
    assert "indexingNote" not in world.graph.record


@pytest.mark.asyncio
async def test_a_service_keep_with_no_indexing_pipeline_to_restore_membership_is_a_refusal() -> None:
    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=True)
    world.processor.indexing_pipeline = None
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    assert world.refused
    assert world.graph.record["indexingStatus"] != COMPLETED


@pytest.mark.asyncio
async def test_a_service_keep_in_keep_old_copy_mode_stays_on_the_old_copy_and_drops_the_pending_one() -> None:
    """The record kept X and named a replacement Y as pending; the count says keep: X stays, Y is dropped and released."""
    from app.services.graph_db.common.record_visibility import PENDING_VIRTUAL_RECORD_ID

    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=False)
    original = world.ep._orchestrate_via_services

    def with_pending(**kwargs: object):  # noqa: ANN202
        world.graph.record[PENDING_VIRTUAL_RECORD_ID] = "v-pending"
        return original(**kwargs)

    world.stack.enter_context(patch.object(world.ep, "_orchestrate_via_services", side_effect=with_pending))
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    record = world.graph.record
    assert not world.refused
    assert record["virtualRecordId"] == VRID and record["indexingStatus"] == COMPLETED
    assert PENDING_VIRTUAL_RECORD_ID not in record
    assert world.cleanup.call_args.args[1] == "v-pending"
    world.processor.indexing_pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with(VRID)


@pytest.mark.asyncio
async def test_a_service_keep_on_an_unshared_virtual_record_id_stays_on_it_and_touches_no_membership() -> None:
    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=False)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    record = world.graph.record
    assert seen["vrid_at_parse"] == VRID and record["virtualRecordId"] == VRID
    world.rewrite.assert_not_awaited()
    assert record["indexingStatus"] == COMPLETED and record["indexingNote"] == PDF_PAGE_LIMIT_NOTE and not world.refused


@pytest.mark.asyncio
async def test_an_event_with_no_org_anywhere_fails_instead_of_deciding_under_an_empty_org() -> None:
    from app.core.org_context import MissingOrgContext

    world = World(searchable(indexingStatus=COMPLETED, pageCount=700), extra_payload={"orgId": None})
    blob = MagicMock()
    blob.get_actual_content_path = AsyncMock(return_value=None)
    world.processor.sink_orchestrator = MagicMock(blob_storage=blob)
    world.count(600)
    with pytest.raises(MissingOrgContext):
        await world.run(UPDATE)
    assert not world.refused
    blob.get_actual_content_path.assert_not_awaited()
    world.unindex.assert_not_awaited()


# --- a file only pdfium can open: the count comes from pdfium on the bytes that are parsed ----------------------------------


def _counts(*outcomes: object) -> AsyncMock:
    return AsyncMock(side_effect=list(outcomes))


@pytest.mark.asyncio
@pytest.mark.parametrize("early", [Unreadable(), Unavailable()])
async def test_a_keep_eligible_file_only_pdfium_can_open_is_kept_when_pdfium_counts_it_over_the_limit(early: object) -> None:
    world = World(searchable(indexingStatus="QUEUED", pageCount=700))
    world.inspected(None)  # no count from the inspection
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new=_counts(early, 600)):
        await world.run(UPDATE)
    assert not world.indexed and not world.refused
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert world.graph.record["indexingStatus"] == COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("early", [Unreadable(), Unavailable()])
async def test_a_new_file_only_pdfium_can_open_is_refused_before_it_is_parsed_when_pdfium_counts_it_over_the_limit(
    early: object,
) -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.inspected(None)
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new=_counts(early, 600)):
        await world.run()
    assert world.refused and not world.indexed
    world.unindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_file_only_pdfium_can_open_within_the_limit_is_indexed_with_that_count() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.inspected(None)
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new=_counts(Unreadable(), 500)):
        await world.run()
    assert world.indexed and not world.refused
    assert world.graph.record["pageCount"] == 500


@pytest.mark.asyncio
async def test_the_late_check_on_an_inspection_with_no_count_still_only_proceeds() -> None:
    from app.modules.indexing.page_limit import LongDocument, Proceed, check_page_limit

    doc = LongDocument(
        record_id="rec-1", snapshot=searchable(indexingStatus="QUEUED"), org_id="org-1", content=None,
        content_rev=REV, md5=None, inspected=InspectedPdf(b"", False, False, None), keep_eligible=True,
    )
    assert isinstance(await check_page_limit(doc), Proceed)


# --- the service-path keep's re-read and put-back conditions ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_record_that_moved_on_in_both_revision_and_virtual_record_id_is_left_alone() -> None:
    """The re-read's revision check comes first: a newer revision is its writer's, not a refusal."""
    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=False)
    original = world.gp.get_document
    reads = 0

    async def newer(*a: object, **kw: object) -> dict:
        nonlocal reads
        reads += 1
        record = dict(await original(*a, **kw))
        if reads > 1:  # the event's own read comes first; the keep's re-read finds the record moved on
            record.update(contentRev="r-newer", virtualRecordId="v-other")
        return record

    world.gp.get_document = newer
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    assert not world.refused
    world.unindex.assert_not_awaited()
    assert "indexingNote" not in world.graph.record
    assert world.graph.record.get("indexingStatus") != "FILE_TYPE_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_the_put_back_applies_only_while_the_virtual_record_id_is_the_one_read() -> None:
    world, seen = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=False)
    # Fires on the put-back, the only status write here: the id moves after the re-read and before the write.
    world.graph.cas_hook = lambda g: g.record.update(virtualRecordId="v-moved")
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    assert world.graph.record["virtualRecordId"] == "v-moved"
    assert world.graph.record["indexingStatus"] == "IN_PROGRESS", "the put-back applied over a moved virtual record id"
    assert "indexingNote" not in world.graph.record


# --- in process: the gate's count fails, the routing's recount succeeds ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
async def test_a_routing_recount_over_the_limit_keeps_a_keep_eligible_record_on_its_old_copy(shared: bool) -> None:
    """The early count and the gate's count are unavailable, the routing's count is 600: the record had an index, so it is put
    back rather than un-indexed; once the run moved it off a shared id, it goes back to that id and its membership is rewritten."""
    world = World(searchable(indexingStatus="QUEUED"))
    world.inspected(None)
    if shared:
        world.gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"id": "rec-1"}, {"id": "rec-2"}])
    world.stack.enter_context(patch.object(world.ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock))
    world.stack.enter_context(patch.object(world.ep, "_spawn_abandoned_vrid_cleanup"))
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new=_counts(Unavailable(), Unavailable(), 600)):
        with patch("app.events.events.content_revision", return_value=REV):
            await world.run(UPDATE)
    record = world.graph.record
    assert not world.indexed
    assert not world.refused
    assert record["indexingStatus"] == COMPLETED and record["virtualRecordId"] == VRID
    assert record["contentRev"] == "other" and record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    world.unindex.assert_not_awaited()
    restored = world.processor.indexing_pipeline.rewrite_or_delete_vector_membership
    assert restored.await_count == 1, "every keep rewrites the membership of the copy it keeps"


# --- the late keep's re-read and marker ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_re_read_in_the_late_keep_fails_the_event_for_a_retry() -> None:
    world, _ = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=False)
    original = world.gp.get_document
    reads = 0

    async def flaky(*a: object, **kw: object) -> dict:
        nonlocal reads
        reads += 1
        if reads > 1:
            assert kw.get("raise_on_error") is True
            raise RuntimeError("graph unavailable")
        return await original(*a, **kw)

    world.gp.get_document = flaky
    with patch("app.events.events.content_revision", return_value=REV), pytest.raises(RuntimeError):
        await world.run(UPDATE)
    world.notified.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("moved_on", "discards"), [(False, True), (True, False)])
async def test_the_late_keep_discards_the_embed_marker_only_while_the_run_still_owns_the_record(
    moved_on: bool, discards: bool
) -> None:
    world, _ = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=False)
    world.stack.enter_context(patch("app.events.events.marker_of", return_value={"attempt": "a1"}))
    discard = world.stack.enter_context(patch.object(world.ep, "_discard_embed_marker", new_callable=AsyncMock))
    original = world.gp.get_document
    reads = 0

    async def newer(*a: object, **kw: object) -> dict:
        nonlocal reads
        reads += 1
        record = dict(await original(*a, **kw))
        if moved_on and reads > 1:
            record.update(contentRev="r-newer")
        return record

    world.gp.get_document = newer
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run(UPDATE)
    assert (discard.await_count == 1) is discards


@pytest.mark.asyncio
async def test_a_refusal_hands_the_events_virtual_record_id_to_the_un_index() -> None:
    """A retry of a refusal whose first run detached the id: the record has none, the event still carries it."""
    world = World({"indexingStatus": "QUEUED"})
    world.count(600)
    world.extra_payload["virtualRecordId"] = "v-old"
    await world.run()
    assert world.refused
    assert world.unindex.await_args.kwargs["fallback_vrid"] == "v-old"


@pytest.mark.asyncio
async def test_a_late_refusal_hands_the_events_virtual_record_id_to_the_un_index() -> None:
    """The retry's early count is unavailable, so the refusal comes from the late check, after the run has written."""
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unavailable())
    world.inspected(600)
    world.extra_payload["virtualRecordId"] = "v-old"
    await world.run()
    assert world.refused
    assert world.unindex.await_args.kwargs["fallback_vrid"] == "v-old"


@pytest.mark.asyncio
async def test_a_retry_after_a_failed_membership_rewrite_settles_with_the_record_in_the_old_copys_points() -> None:
    """The first run put the record back on its old id and the rewrite raised; the retry's early count is over the limit, so
    it settles at the early gate. That keep must rewrite the membership too."""
    first, _ = _service_world(searchable(indexingStatus="QUEUED", pageCount=700), shared=True)
    first.processor.indexing_pipeline.rewrite_or_delete_vector_membership = AsyncMock(side_effect=RuntimeError("store down"))
    with patch("app.events.events.content_revision", return_value=REV), pytest.raises(RuntimeError):
        await first.run(UPDATE)
    stuck = dict(first.graph.record)
    assert stuck["virtualRecordId"] == VRID

    retry = World(stuck)
    retry.count(600)  # the early gate decides this time
    await retry.run(UPDATE)
    assert not retry.refused
    assert retry.graph.record["indexingStatus"] == COMPLETED and retry.graph.record["virtualRecordId"] == VRID
    retry.processor.indexing_pipeline.rewrite_or_delete_vector_membership.assert_awaited_with(VRID)


@pytest.mark.asyncio
async def test_an_early_keep_rewrites_the_membership_of_the_copy_it_keeps() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(600)
    await world.run(UPDATE)
    world.processor.indexing_pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with(VRID)


@pytest.mark.asyncio
async def test_a_keep_whose_membership_rewrite_fails_is_not_acknowledged() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(600)
    world.processor.indexing_pipeline.rewrite_or_delete_vector_membership = AsyncMock(side_effect=RuntimeError("store down"))
    with pytest.raises(RuntimeError):
        await world.run(UPDATE)
    assert world.graph.record["indexingStatus"] == "QUEUED" and "indexingNote" not in world.graph.record
