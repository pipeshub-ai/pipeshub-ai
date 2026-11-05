"""``count_accessible_entity_records``: the total a listing's first page
states counts only readable records and says when it is a minimum."""
from __future__ import annotations

import pytest

from app.modules.retrieval import entity_permissions
from app.modules.retrieval.entity_permissions import (
    RecordCount,
    count_accessible_entity_records,
)
from tests.unit.modules.retrieval.test_entity_permissions_capped import (
    _context,
    _graph,
    _row,
)


def _n(counted: RecordCount) -> tuple[int, bool]:
    return counted.count, counted.exact


async def _count(graph, **kwargs) -> RecordCount:
    return await count_accessible_entity_records(graph, _context(), entity_id="t1", entity_type="topic", **kwargs)


class TestCount:
    async def test_every_readable_record_is_counted_exactly(self) -> None:
        graph = _graph([_row(f"r{i}") for i in range(130)], capped=False)
        assert _n(await _count(graph)) == (130, True)

    async def test_unreadable_records_are_not_counted(self) -> None:
        rows = [_row("a"), _row("b", connector="conf-1"), _row("c", connector="conf-1")]
        graph = _graph(rows, capped=False, permitted={"b"})
        assert _n(await _count(graph)) == (2, True)

    async def test_a_capped_entity_is_a_minimum(self) -> None:
        graph = _graph([_row(f"r{i}") for i in range(5)], capped=True)
        assert _n(await _count(graph)) == (5, False)

    async def test_past_the_count_cap_is_a_minimum(self, monkeypatch) -> None:
        monkeypatch.setattr(entity_permissions, "COUNT_MAX_RECORDS", 10)
        graph = _graph([_row(f"r{i}") for i in range(25)], capped=False)
        assert _n(await _count(graph)) == (10, False)

    async def test_a_count_of_exactly_the_cap_with_nothing_more_is_exact(self, monkeypatch) -> None:
        monkeypatch.setattr(entity_permissions, "COUNT_MAX_RECORDS", 10)
        graph = _graph([_row(f"r{i}") for i in range(10)], capped=False)
        assert _n(await _count(graph)) == (10, True)

    async def test_counting_from_a_cursor_counts_only_what_follows(self) -> None:
        graph = _graph([_row(f"r{i}") for i in range(30)], capped=False)
        assert _n(await _count(graph, cursor="20")) == (10, True)

    async def test_a_scan_that_runs_out_among_unreadable_records_is_a_minimum(self, monkeypatch) -> None:
        """The realistic "at least": few readable records spread over more candidates than one count scans."""
        monkeypatch.setattr(entity_permissions, "SEARCH_SCOPE_MAX_SCAN", 200)
        rows = [_row(f"r{i}", connector="conf-1") for i in range(600)]
        graph = _graph(rows, capped=False, permitted={f"r{i}" for i in range(0, 600, 50)})
        counted = await _count(graph)
        assert counted.exact is False
        assert counted.count == 4

    async def test_a_count_stopped_by_its_deadline_is_a_minimum(self, monkeypatch) -> None:
        monkeypatch.setattr(entity_permissions, "SEARCH_DEADLINE_SECONDS", 0.0)
        graph = _graph([_row(f"r{i}") for i in range(1200)], capped=False)
        counted = await _count(graph)
        assert counted.exact is False
        assert 0 < counted.count < 1200

    async def test_an_unsupported_entity_type_is_refused(self) -> None:
        graph = _graph([], capped=False)
        with pytest.raises(ValueError, match="Unsupported entity type"):
            await count_accessible_entity_records(graph, _context(), entity_id="t1", entity_type="person")
