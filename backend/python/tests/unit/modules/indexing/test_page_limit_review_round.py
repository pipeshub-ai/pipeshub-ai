"""Page-limit behaviours found in review: where a keep is decided, which writes it makes, and what counts as an index."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.events.events import EventProcessor
from app.modules.indexing import page_limit
from app.modules.parsers.pdf.page_count import Unavailable, Unreadable
from app.services.messaging.config import IndexingEvent
from app.utils.user_errors import PDF_PAGE_LIMIT_NOTE
from tests.unit.modules.indexing.test_page_limit import (
    COMPLETED,
    REV,
    VRID,
    World,
    searchable,
)

if TYPE_CHECKING:
    from app.modules.parsers.pdf.untrusted_pdf import InspectedPdf

UPDATE = "updateRecord"


# --- the event's org id -----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_event_without_an_org_id_refuses_under_the_stored_records_org() -> None:
    """The refusal's clean-up must look in the record's org, as the rest of the event does."""
    world = World({"indexingStatus": "QUEUED"}, extra_payload={"orgId": None})
    world.count(600)
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
