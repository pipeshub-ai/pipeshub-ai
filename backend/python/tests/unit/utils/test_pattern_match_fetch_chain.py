"""A grep hit must be fetchable by the Record ID the LLM is shown.

Runs the chain the agent depends on: merge_pattern_match_results puts the
permission-checked graph record in the map, render_pattern_match_hint shows it
to the LLM with a Record ID, and fetch_full_record resolves that ID back to the
record's content. Neo4j returns graph records with ``id``; ArangoDB returns the
raw document, where ``id`` was stored as ``_key``. Both must work.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from app.utils.chat_helpers import RecordIdShortener
from app.utils.fetch_full_record import _fetch_multiple_records_impl
from app.utils.pattern_match import merge_pattern_match_results, render_pattern_match_hint

ORG, USER, RECORD_ID, VRID = "org-1", "user-1", "rec-bob", "vrid-shared"


def _graph_record(id_field: str, *, name: str = "Doc1") -> dict:
    return {
        id_field: RECORD_ID,
        "orgId": ORG,
        "recordName": name,
        "recordType": "FILE",
        "origin": "CONNECTOR",
        "connectorName": "DRIVE",
        "connectorId": "conn-1",
        "externalRecordId": "ext-1",
        "virtualRecordId": VRID,
        "indexingStatus": ProgressStatus.COMPLETED.value,
        "version": 0,
    }


def _graph_provider(graph_record: dict) -> MagicMock:
    graph = MagicMock()
    graph.config_service = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers())
    # The shared VRID resolves to the record this user can read.
    graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={VRID: RECORD_ID})
    graph.get_records_by_record_ids = AsyncMock(return_value=[graph_record])
    graph.check_record_access_with_details = AsyncMock(return_value={"record": {"_key": RECORD_ID}})
    graph.get_document = AsyncMock(return_value=graph_record)
    return graph


async def _merge(graph: MagicMock, **raw_fields) -> tuple[list[dict], dict]:
    vr_map: dict = {}
    raw = {"virtual_record_id": VRID, "match_count": "3", "match_preview": "…Q3 revenue…", **raw_fields}
    entries = await merge_pattern_match_results(
        raw_records=[raw],
        virtual_record_id_to_result=vr_map,
        user_id=USER,
        org_id=ORG,
        blob_store=MagicMock(),
        graph_provider=graph,
        is_multimodal_llm=False,
        logger_instance=MagicMock(),
    )
    return entries, vr_map


async def _fake_get_record(vrid, vr_map, *_args, **_kwargs) -> None:
    vr_map[vrid] = {
        "id": RECORD_ID,
        "record_name": "Doc1",
        "virtual_record_id": vrid,
        "block_containers": {"blocks": [{"data": "Q3 revenue grew 12%"}]},
    }


@pytest.mark.parametrize("id_field", ["id", "_key"], ids=["neo4j_shape", "arango_shape"])
async def test_grep_hit_is_shown_with_its_record_id_and_fetches_by_it(id_field):
    graph = _graph_provider(_graph_record(id_field))

    entries, vr_map = await _merge(graph)
    hint = render_pattern_match_hint(entries, vr_map)

    assert f"Record ID: {RECORD_ID}" in hint
    assert "Name: Doc1" in hint
    assert "Matched Text: …Q3 revenue…" in hint
    assert "`knowledgegraph__fetch_record` with its record_id" in hint

    blob_store = MagicMock()
    blob_store.config_service.get_config = AsyncMock(return_value={})
    with patch("app.utils.fetch_full_record.get_record", AsyncMock(side_effect=_fake_get_record)), \
         patch("app.utils.fetch_full_record.excluded_demo_connector_ids", AsyncMock(return_value=frozenset())):
        result = await _fetch_multiple_records_impl(
            [RECORD_ID], vr_map, graph_provider=graph, blob_store=blob_store, org_id=ORG, user_id=USER,
        )

    assert result["ok"] is True, result
    assert result["records"][0]["record_name"] == "Doc1"
    assert result["records"][0]["block_containers"]["blocks"][0]["data"] == "Q3 revenue grew 12%"
    assert result["not_available_ids"] == []


async def test_neo4j_shaped_hit_is_served_from_the_map_without_a_second_access_check():
    graph = _graph_provider(_graph_record("id"))
    _entries, vr_map = await _merge(graph)

    blob_store = MagicMock()
    blob_store.config_service.get_config = AsyncMock(return_value={})
    with patch("app.utils.fetch_full_record.get_record", AsyncMock(side_effect=_fake_get_record)):
        result = await _fetch_multiple_records_impl(
            [RECORD_ID], vr_map, graph_provider=graph, blob_store=blob_store, org_id=ORG, user_id=USER,
        )

    assert result["ok"] is True
    graph.check_record_access_with_details.assert_not_awaited()


async def test_record_name_comes_from_the_readable_record_not_the_stored_file():
    """Shared content is stored once under the first owner's name; the reader sees their own."""
    graph = _graph_provider(_graph_record("id", name="Doc1"))
    entries, vr_map = await _merge(graph, record_name="Q3 layoffs plan")
    hint = render_pattern_match_hint(entries, vr_map)
    assert "Name: Doc1" in hint
    assert "Q3 layoffs plan" not in hint


async def test_unreadable_hit_is_neither_shown_nor_put_in_the_map():
    graph = _graph_provider(_graph_record("id"))
    graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={})
    entries, vr_map = await _merge(graph)
    assert entries == []
    assert vr_map == {}


def test_full_record_id_passes_the_shortener_unchanged():
    # The pattern-match hint prints full ids even when shortening is on.
    shortener = RecordIdShortener()
    assert shortener.resolve(RECORD_ID) == RECORD_ID
