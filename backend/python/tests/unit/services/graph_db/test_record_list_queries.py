"""The record list queries: bind parameters and failed reads.

ArangoDB rejects a query that is sent a bind parameter it does not declare, as
well as one that is missing a parameter it uses. The list queries used to fail
that way on every call, and the error was swallowed into an empty page. These
tests compare what each query declares with what it is sent, for every view
and filter combination, and check that a failed read raises.

The All Records list is the Knowledge Hub listing
(``get_knowledge_hub_connector_page_v3``, one page per connector); a KB's own
list is ``list_kb_records``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

_BIND = re.compile(r"@@?[A-Za-z_][A-Za-z0-9_]*")


def _declared(query: str) -> set[str]:
    return {m.lstrip("@") if not m.startswith("@@") else m[1:] for m in _BIND.findall(query)}


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    return provider


FILTERS = {
    "search": "report", "record_types": ["FILE"], "origins": ["UPLOAD"], "connectors": ["DRIVE"],
    "indexing_status": ["COMPLETED"], "date_from": 1, "date_to": 2,
}

HUB_FILTERS = {
    "search_query": "report", "node_types": ["record"], "record_types": ["FILE"],
    "indexing_status": ["COMPLETED"], "created_at": {"gte": 1, "lte": 2}, "updated_at": {"gte": 1, "lte": 2},
    "size": {"gte": 1, "lte": 2}, "origins": ["CONNECTOR"], "connector_ids": ["app1"],
    "record_group_ids": ["g1"],
}

# start_id, start_type, flatten
HUB_VIEWS = {
    "all records": (None, "app", True),
    "app children": (None, "app", False),
    "group children": ("g1", "recordGroup", False),
    "folder children": ("f1", "folder", False),
    "below a folder": ("f1", "folder", True),
}


class _Hub:
    """Stands in for ArangoDB under the Knowledge Hub listing: answers each
    statement with enough for the listing to send the next one, so every
    statement of a view is sent, and remembers what each was sent."""

    def __init__(self, provider: ArangoHTTPProvider, *, opens: bool, from_app: bool) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: set[str] = set()
        self._names = {
            **{text: name for name, text in provider._kh_v3_listing_aql().items()},
            **{text: f"check:{name}" for name, text in provider._kh_v3_check_aql().items()},
        }
        row = {"id": "r1", "parentOptions": [{"id": "p1", "nodeType": "record"}]}
        target = {"id": "p1", "vrid": None, "connectorId": "app1", "indexingStatus": None,
                  "isInternal": False, "ok": False, "walk": True}
        self._answers: dict[str, list[Any]] = {
            "app": [{"handle": "apps/app1", "opens": opens}],
            "declared": ["recordGroups/g1"],
            "group_scope": [["recordGroups/g1", False]],
            "seeds": ["records/s1"],
            "chain_top_groups": [{"ownGroups": ["g9"], "ids": ["s1"]}],
            "chain_top_details": [{"id": "s1", "handle": "records/s1", "parents": ["p9"],
                                   "ownGroups": [{"id": "g9", "deleted": False}]}],
            "chain_top_facts": [{"hidden": [], "underApp": []}],
            "lineage": [{"ancestors": ["a1"], "declared": ["g1"]}],
            "browse_start": [{"handle": "records/f1", "fromApp": from_app, "crumbNodes": [], "crumbEdges": []}],
            "enrich": [{"i": 0, "row": row}],
            "check:targets": [target],
            "check:seeds": [{"node": "p1", "seed": "s2"}],
            "slim": [["records/r1", "record", "a"]],
            "connector": ["app1"],
        }
        # A walk asks level by level until a level finds nothing new.
        self._levels = {"step_app": ["records/r1"], "step_open": ["records/r2"],
                        "step_browse": ["records/r3"], "step_browse_open": ["records/r3"]}

    async def __call__(self, query: str, bind_vars: dict[str, Any] | None = None, **_: Any) -> list[Any]:
        self.calls.append((query, dict(bind_vars or {})))
        name = self._names.get(query)
        if name is None:
            name = "slim" if "kh_handles" in query else "connector"
        self.sent.add(name)
        if name in self._levels:
            found = [h for h in self._levels[name] if h not in bind_vars["kh_frontier"]]
            self._levels[name] = []
            return found
        return list(self._answers.get(name, []))


def _arango_hub(*, opens: bool, from_app: bool) -> tuple[ArangoHTTPProvider, _Hub]:
    provider = _arango()
    hub = _Hub(provider, opens=opens, from_app=from_app)
    provider.http_client.execute_aql = hub
    # The App is read by key for the scope of a listing that starts at it.
    provider.get_document = AsyncMock(return_value={"name": "Drive", "type": "DRIVE"})
    return provider, hub


@pytest.mark.parametrize("view", sorted(HUB_VIEWS))
@pytest.mark.parametrize("opens", [False, True])
@pytest.mark.parametrize("from_app", [False, True])
@pytest.mark.parametrize("filtered", [False, True])
async def test_all_records_sends_exactly_the_binds_it_declares(view, opens, from_app, filtered) -> None:
    provider, hub = _arango_hub(opens=opens, from_app=from_app)
    start_id, start_type, flatten = HUB_VIEWS[view]

    page = await provider.get_knowledge_hub_connector_page_v3(
        "app1", "org1", ["uk1", "org1"], ["app1"], ["s1", "g1"], 10,
        flatten=flatten, sort_field="updatedAt", sort_dir="DESC",
        filters=HUB_FILTERS if filtered else None, start_id=start_id, start_type=start_type,
        include_scope=True,
    )

    assert [row["id"] for row in page["rows"]] == ["r1"], "the listing stopped before its page"
    assert {"app", "slim", "enrich", "check:targets"} <= hub.sent, hub.sent
    for query, binds in hub.calls:
        assert _declared(query) == set(binds), query


async def test_every_knowledge_hub_statement_is_reached_by_some_view() -> None:
    """Or a statement with a wrong bind would pass the test above unseen."""
    reached: set[str] = set()
    for start_id, start_type, flatten in HUB_VIEWS.values():
        for opens in (False, True):
            for from_app in (False, True):
                provider, hub = _arango_hub(opens=opens, from_app=from_app)
                await provider.get_knowledge_hub_connector_page_v3(
                    "app1", "org1", ["uk1", "org1"], ["app1"], ["s1", "g1"], 10,
                    flatten=flatten, start_id=start_id, start_type=start_type, include_scope=True,
                )
                reached |= hub.sent
    provider = _arango()
    statements = {*provider._kh_v3_listing_aql(), *(f"check:{n}" for n in provider._kh_v3_check_aql())}
    assert statements <= reached, sorted(statements - reached)


@pytest.mark.parametrize("folder_id", [None, "f1"])
@pytest.mark.parametrize("filtered", [False, True])
async def test_kb_records_sends_exactly_the_binds_it_declares(folder_id, filtered) -> None:
    provider = _arango()
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[{"records": [], "total": 0}], [[]]])
    filters = FILTERS if filtered else dict.fromkeys(FILTERS)
    filters = {k: v for k, v in filters.items() if k != "permissions"}
    await provider.list_kb_records(
        "kb1", "uk1", "org1", 0, 10, sort_by="recordName", sort_order="asc", folder_id=folder_id, **filters,
    )
    for call in provider.execute_query.await_args_list:
        assert _declared(call.args[0]) == set(call.kwargs["bind_vars"])


async def test_an_unknown_sort_field_is_not_spliced_into_the_query() -> None:
    provider = _arango()
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[{"records": [], "total": 0}], [[]]])
    await provider.list_kb_records(
        "kb1", "uk1", "org1", 0, 10, None, None, None, None, None, None, None,
        sort_by="x REMOVE record IN records", sort_order="asc; drop",
    )
    query = provider.execute_query.await_args_list[0].args[0]
    assert "REMOVE" not in query and "drop" not in query
    assert "SORT record.recordName ASC" in query


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    return provider


@pytest.mark.parametrize("flatten", [True, False])
@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_all_records_read_raises(backend, flatten) -> None:
    """An empty page tells the user they have no records; a failure must not."""
    if backend == "arango":
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("graph down"))
    else:
        provider = _neo4j()
    with pytest.raises(RuntimeError, match="graph down"):
        await provider.get_knowledge_hub_connector_page_v3(
            "app1", "org1", ["uk1"], ["app1"], ["r1"], 10, flatten=flatten,
        )


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_kb_records_read_raises(backend) -> None:
    if backend == "arango":
        provider = _arango()
        provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        provider.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    else:
        provider = _neo4j()
    with pytest.raises(RuntimeError, match="graph down"):
        await provider.list_kb_records(
            "kb1", "uk1", "org1", 0, 10, None, None, None, None, None, None, None, "recordName", "asc",
        )


@pytest.mark.parametrize("flatten", [True, False])
async def test_a_listed_row_takes_its_size_from_the_file_when_the_record_has_none(flatten) -> None:
    """An older record carries its size on the File node alone, on both backends."""
    neo4j = _neo4j()
    neo4j.client.execute_query = AsyncMock(return_value=[])
    await neo4j.get_knowledge_hub_connector_page_v3("app1", "org1", ["uk1"], ["app1"], ["r1"], 10, flatten=flatten)
    listing = neo4j.client.execute_query.await_args.args[0]
    assert "OPTIONAL MATCH (node)-[:IS_OF_TYPE]->(kh_f:File)" in listing
    assert "head(collect(kh_f.sizeInBytes)) AS kh_size" in listing
    assert "sizeInBytes: coalesce(node.sizeInBytes, kh_size)" in listing

    enrich = _arango()._kh_v3_listing_aql()["enrich"]
    assert re.search(
        r"sizeInBytes: \(node\.sizeInBytes != null \? node\.sizeInBytes"
        r" : FIRST\(FOR kh_f IN 1\.\.1 OUTBOUND node isOfType RETURN kh_f\.sizeInBytes\)\)",
        enrich,
    )
