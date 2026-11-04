"""A citation names the connector instance a record came from, and under a
connected app's limit only an app the limit covers whole."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.chat_helpers import as_blob_record, attach_connector_instance_names

WHOLE_APP = {"apps": ["c-in"], "recordGroups": [], "records": []}


def _graph(**names: str) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=lambda key, _c: {"_key": key, "name": names[key]} if key in names else None)
    return graph


@pytest.mark.asyncio
async def test_without_a_graph_nothing_is_named() -> None:
    records = [{"connectorId": "c-in"}]

    await attach_connector_instance_names(None, records, None)

    assert "connectorInstanceName" not in records[0]


@pytest.mark.asyncio
async def test_a_record_with_no_connector_asks_nothing() -> None:
    graph = _graph()
    records = [{"_key": "r1"}, None]

    await attach_connector_instance_names(graph, records, None)

    assert "connectorInstanceName" not in records[0]
    graph.get_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_limit_every_record_is_named() -> None:
    records = [{"connectorId": "c-in"}, {"connectorId": "c-out"}]

    await attach_connector_instance_names(_graph(**{"c-in": "Board finance", "c-out": "HR Drive"}), records, None)

    assert [r["connectorInstanceName"] for r in records] == ["Board finance", "HR Drive"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["graph", None])
async def test_an_app_the_limit_does_not_cover_whole_is_never_named_or_looked_up(provider) -> None:
    graph = _graph(**{"c-in": "Board finance", "c-out": "HR Drive"})
    inside = {"connectorId": "c-in"}
    outside = {"connectorId": "c-out", "connectorInstanceName": "HR Drive"}

    await attach_connector_instance_names(graph if provider else None, [inside, outside], WHOLE_APP)

    assert "connectorInstanceName" not in outside
    assert [c.args[0] for c in graph.get_document.await_args_list] == (["c-in"] if provider else [])
    assert inside.get("connectorInstanceName") == ("Board finance" if provider else None)


@pytest.mark.asyncio
async def test_a_file_limit_names_no_app() -> None:
    graph = _graph(**{"c-in": "Board finance"})
    record = {"connectorId": "c-in"}

    await attach_connector_instance_names(graph, [record], {"apps": [], "recordGroups": [], "records": ["r1"]})

    assert "connectorInstanceName" not in record
    graph.get_document.assert_not_awaited()


def test_a_graph_record_cited_as_it_is_carries_its_connector_name() -> None:
    record = as_blob_record({"_key": "r1", "recordName": "a.txt", "connectorInstanceName": "Board finance"})

    assert record["connector_instance_name"] == "Board finance"


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", [AsyncMock(return_value=None), AsyncMock(side_effect=RuntimeError("graph down"))])
async def test_a_failed_or_empty_lookup_is_not_repeated_for_the_next_record(lookup) -> None:
    graph = MagicMock()
    graph.get_document = lookup
    lookup.reset_mock()

    for _ in range(3):
        record = {"connectorId": "c-gone"}
        await attach_connector_instance_names(graph, [record], None)
        assert "connectorInstanceName" not in record

    assert lookup.await_count == 1
