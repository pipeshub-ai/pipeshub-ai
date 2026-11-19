"""``delete_unreached_taxonomy_points``: the helper the record delete and the un-index routine share."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.indexing_exceptions import IndexingError
from app.modules.indexing.taxonomy_cleanup import delete_unreached_taxonomy_points

REFS = [{"id": "t1", "type": "topic"}]


@pytest.mark.parametrize("refs", [None, [], "x", {"id": "t1"}])
async def test_nothing_usable_does_nothing(refs) -> None:
    store, graph = MagicMock(), MagicMock()
    store.delete_unreached_entities = AsyncMock()
    await delete_unreached_taxonomy_points(store, graph, "org-1", "r1", refs)
    store.delete_unreached_entities.assert_not_awaited()


async def test_the_store_asks_the_graph_which_entities_no_record_reaches() -> None:
    store, graph = MagicMock(), MagicMock()
    store.delete_unreached_entities = AsyncMock()
    graph.get_unreached_taxonomy_entities = AsyncMock(return_value={("topic", "t1")})
    await delete_unreached_taxonomy_points(store, graph, "org-1", "r1", REFS)
    org_id, refs, lookup = store.delete_unreached_entities.await_args.args
    assert (org_id, refs) == ("org-1", REFS)
    assert await lookup(REFS) == {("topic", "t1")}
    graph.get_unreached_taxonomy_entities.assert_awaited_once_with(REFS, "org-1")


async def test_a_failure_raises_so_the_caller_retries() -> None:
    store, graph = MagicMock(), MagicMock()
    store.delete_unreached_entities = AsyncMock(side_effect=RuntimeError("down"))
    with pytest.raises(IndexingError) as raised:
        await delete_unreached_taxonomy_points(store, graph, "org-1", "r1", REFS)
    assert raised.value.details == {"org_id": "org-1", "record_id": "r1"}
    assert isinstance(raised.value.__cause__, RuntimeError)
