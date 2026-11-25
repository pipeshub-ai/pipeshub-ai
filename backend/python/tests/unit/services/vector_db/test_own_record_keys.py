"""Deleting content that a thousand records share read each of them on its own, at once, for every delete: the
graph's connection pool filled and every other query failed."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.vector_db import membership
from app.services.vector_db.membership import own_record_keys

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _graph(owners: dict[str, str | None], missing: tuple[str, ...] = ()) -> AsyncMock:
    async def read(collection: str, field: str, values: list, return_fields: list, **kwargs: object) -> list[dict]:
        assert (collection, field, return_fields) == ("records", "id", ["id", "orgId"])
        assert kwargs == {"raise_on_error": True}
        return [{"id": v, "orgId": owners[v]} for v in values if v not in missing]

    graph = AsyncMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=list(owners))
    graph.get_nodes_by_field_in = AsyncMock(side_effect=read)
    return graph


async def test_a_thousand_sharing_records_cost_a_few_queries() -> None:
    graph = _graph({f"r{i}": "org-1" for i in range(1200)})
    assert len(await own_record_keys(graph, "v1", "org-1")) == 1200
    assert graph.get_nodes_by_field_in.await_count == 3
    graph.get_document.assert_not_awaited()


async def test_only_records_of_another_org_are_left_out() -> None:
    graph = _graph({"mine": "org-1", "theirs": "org-2", "unnamed": None, "gone": "org-1"}, missing=("gone",))
    assert await own_record_keys(graph, "v1", "org-1") == ["mine", "unnamed", "gone"]


async def test_a_failed_read_raises() -> None:
    graph = _graph({"mine": "org-1"})
    graph.get_nodes_by_field_in = AsyncMock(side_effect=ConnectionError("graph down"))
    with pytest.raises(ConnectionError):
        await own_record_keys(graph, "v1", "org-1")


async def test_no_records_reads_nothing_more() -> None:
    graph = _graph({})
    assert await own_record_keys(graph, "v1", "org-1") == []
    graph.get_nodes_by_field_in.assert_not_awaited()
    assert membership.OWNER_READ_BATCH >= 100
