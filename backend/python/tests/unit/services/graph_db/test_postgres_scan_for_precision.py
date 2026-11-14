"""The cleanup's scan on the PostgreSQL graph: the same ``get_records_by_status`` call, with a narrower status list."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.connectors.services import vector_store_rebuild as rebuild
from app.services.graph_db.postgres.lookups import LookupsMixin

pytestmark = pytest.mark.asyncio


def _graph(rows: list[dict]) -> LookupsMixin:
    graph = LookupsMixin.__new__(LookupsMixin)
    graph.logger = logging.getLogger("t")
    graph._lrows = AsyncMock(return_value=rows)
    return graph


async def test_the_scan_asks_the_postgres_graph_for_in_progress_records_only_while_indexing_waits() -> None:
    graph = _graph([])
    with patch("app.services.graph_db.postgres.lookups.current_org_id", return_value="o"):
        assert await rebuild.find_busy_connectors(graph, [("o", "app-1")], (ProgressStatus.IN_PROGRESS.value,)) == []
        await rebuild.find_busy_connectors(graph, [("o", "app-1")])
    narrow, wide = (call.args for call in graph._lrows.await_args_list)
    assert narrow[0].count("= ANY($4::text[])") == 1 and "NOT coalesce" in narrow[0]  # the status filter is positive
    status_filters = [a for a in narrow if isinstance(a, list) and a and a[0] in {s.value for s in ProgressStatus}]
    assert status_filters == [[ProgressStatus.IN_PROGRESS.value]]
    assert [a for a in wide if isinstance(a, list) and a and a[0] in {s.value for s in ProgressStatus}] == [
        [ProgressStatus.IN_PROGRESS.value, ProgressStatus.QUEUED.value],
    ]


async def test_a_postgres_read_that_fails_stops_the_cleanup_not_the_other_way_round() -> None:
    graph = _graph([])
    graph._lrows = AsyncMock(side_effect=RuntimeError("pool down"))
    with patch("app.services.graph_db.postgres.lookups.current_org_id", return_value="o"), \
            patch.object(rebuild, "get_coordinator", return_value=None), pytest.raises(rebuild.VectorStoreRebuildConflictError):
        await rebuild.assert_no_indexing_in_flight(graph, [("o", "app-1")], waiting_for_precision=True)
