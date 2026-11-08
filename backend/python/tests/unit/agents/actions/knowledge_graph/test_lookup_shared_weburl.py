"""A web URL that several records share.

Two connectors can sync the same page: two RSS instances on one feed, a site
crawled twice, a file reached through two shares. Each makes its own record
with the same ``webUrl``. The lookup read one record per URL for the whole
organisation before any access check, so a user who could open only another
copy was told "not found", and which copy came back was down to the graph.

The graph is an autospec of ArangoHTTPProvider. Its single-record URL read
answers the first record, as ``LIMIT 1`` does; its batch read answers them all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, create_autospec

from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog, ConnectorInfo
from app.agents.actions.knowledge_graph.resolver import RecordResolver
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

ORG_ID = "org-1"
URL = "https://news.example.com/articles/launch"


def _article(record_id: str, connector_id: str) -> FileRecord:
    return FileRecord(
        id=record_id,
        record_name="Launch day",
        record_type=RecordType.FILE,
        external_record_id=URL,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.RSS,
        connector_id=connector_id,
        weburl=URL,
        indexing_status="COMPLETED",
        is_file=False,
        extension="html",
    )


FIRST = _article("rec-first", "rss-a")
SECOND = _article("rec-second", "rss-b")


def _graph(records: list[FileRecord], readable: set[str], extra_rows: Sequence[dict[str, Any]] = ()) -> MagicMock:
    """``extra_rows`` come first at the URL and have no record behind them."""
    graph = create_autospec(ArangoHTTPProvider, instance=True)
    by_id = {r.id: r for r in records}

    def one_by_weburl(url: str, *_: object, **__: object) -> FileRecord | None:
        return next((r for r in records if r.weburl == url), None)

    def all_by_weburls(urls: list[str], org_id: str, per_address: int = 2, *_: object, **__: object) -> list[dict[str, Any]]:
        assert org_id == ORG_ID
        rows = [*extra_rows, *({"id": r.id, "recordType": r.record_type.value, "connectorName": r.connector_name.value,
                                "webUrl": r.weburl, "isDependentNode": False} for r in records if r.weburl in urls)]
        return rows[:per_address]

    graph.get_record_by_weburl.side_effect = one_by_weburl
    graph.get_records_by_weburls.side_effect = all_by_weburls
    graph.get_record_by_id.side_effect = lambda record_id, *_, **__: by_id.get(record_id)
    graph.get_document.return_value = None
    graph.get_knowledge_hub_node_access.side_effect = (
        lambda node_id, **_: {"id": node_id, "nodeType": "record"} if node_id in readable else None
    )
    return graph


def _resolver(graph: MagicMock) -> RecordResolver:
    return RecordResolver(
        graph_provider=graph,
        catalog=ConnectorCatalog([ConnectorInfo(id="rss-a", name="News A", type="RSS"),
                                  ConnectorInfo(id="rss-b", name="News B", type="RSS")]),
        org_id=ORG_ID,
        user_id="user-1",
        user_key="user-key-1",
        folder_mime_types=[],
    )


async def test_the_copy_the_user_can_open_is_found_when_another_copy_comes_first() -> None:
    result = await _resolver(_graph([FIRST, SECOND], readable={SECOND.id})).resolve_many([URL])

    assert [m.id for m in result.matches] == [SECOND.id]
    assert result.not_found_identifiers == [] and result.ambiguous is False


async def test_every_copy_the_user_can_open_is_returned_and_marked_ambiguous() -> None:
    result = await _resolver(_graph([FIRST, SECOND], readable={FIRST.id, SECOND.id})).resolve_many([URL])

    assert sorted(m.id for m in result.matches) == [FIRST.id, SECOND.id]
    assert result.ambiguous is True


async def test_a_copy_keeps_its_typed_context() -> None:
    result = await _resolver(_graph([FIRST, SECOND], readable={SECOND.id})).resolve_many([URL])

    match = result.matches[0]
    assert match.name == "Launch day" and match.connector_name == Connectors.RSS.value
    assert match.context_block and URL in match.context_block


async def test_no_copy_the_user_can_open_reads_as_not_found() -> None:
    result = await _resolver(_graph([FIRST, SECOND], readable=set())).resolve_many([URL])

    assert result.matches == [] and result.not_found_identifiers == [URL]


async def test_a_copy_that_cannot_be_built_does_not_hide_the_others() -> None:
    malformed = {"id": "rec-malformed", "webUrl": URL, "indexingStatus": 5}    # no record behind it, and a status that is not text
    graph = _graph([SECOND], readable={"rec-malformed", SECOND.id}, extra_rows=[malformed])

    result = await _resolver(graph).resolve_many([URL])

    assert [m.id for m in result.matches] == [SECOND.id]
    assert result.not_found_identifiers == []
