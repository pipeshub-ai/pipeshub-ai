"""The reads behind find_records_by_value's listing: the record fields a page is
ordered and filtered by, and the matched values shown per record."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.named_entities.graph_ops import (
    RECORD_HITS_AQL,
    RECORD_HITS_CYPHER,
    VALUES_IN_RECORDS_AQL,
    VALUES_IN_RECORDS_CYPHER,
    NamedEntityGraph,
    NamedEntityQuery,
)


class _Recorder:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows = rows or []

    async def __call__(self, statement: str, binds: dict[str, Any]) -> list[Any]:
        self.calls.append((statement, binds))
        return self.rows


def test_record_hits_carry_the_type_and_the_modification_time_on_both_backends() -> None:
    assert "recordType: r.recordType" in RECORD_HITS_AQL
    assert "sortTs: r.sourceLastModifiedTimestamp || r.updatedAtTimestamp || 0" in RECORD_HITS_AQL
    assert "r.recordType AS recordType" in RECORD_HITS_CYPHER
    # A zero modification time falls through to the update time on both backends.
    assert "CASE WHEN coalesce(r.sourceLastModifiedTimestamp, 0) <> 0" in RECORD_HITS_CYPHER


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "statement"), [("arango", VALUES_IN_RECORDS_AQL), ("neo4j", VALUES_IN_RECORDS_CYPHER)])
async def test_values_are_read_for_the_page_with_the_value_constraint(dialect: str, statement: str) -> None:
    execute = _Recorder([{"recordId": "r1", "names": ["USD 52,000.00"]}])
    query = NamedEntityQuery(amount_min=50_000, amount_max=1e308, currency="USD")

    found = await NamedEntityGraph(execute, dialect).values_in_records("org", query, ["r1", "r2", "r1"], 10)

    assert found == {"r1": ["USD 52,000.00"]}
    ((sent, binds),) = execute.calls
    assert sent == statement
    assert binds["recordIds"] == ["r1", "r2"]
    assert (binds["orgId"], binds["perRecord"]) == ("org", 10)
    assert (binds["amountMin"], binds["currency"]) == (50_000, "USD")


@pytest.mark.asyncio
async def test_each_value_constraint_is_read_and_a_records_names_keep_their_order_once_and_capped() -> None:
    execute = _Recorder([{"recordId": "r1", "names": ["USD 900", "USD 50"]}])
    query = NamedEntityQuery(amount_min=1, amount_max=2, percent_min=0.1, percent_max=0.2)

    found = await NamedEntityGraph(execute, "neo4j").values_in_records("org", query, ["r1"], 1)

    assert len(execute.calls) == 2
    assert found == {"r1": ["USD 900"]}


def test_values_are_ordered_largest_first_and_never_null_on_both_backends() -> None:
    assert "SORT value.size DESC" in VALUES_IN_RECORDS_AQL and "v.name != null" in VALUES_IN_RECORDS_AQL
    assert "ORDER BY coalesce(v.amountFloat, v.siValue, v.numericValue, v.startMs, 0) DESC" in VALUES_IN_RECORDS_CYPHER
    assert "v.name IS NOT NULL" in VALUES_IN_RECORDS_CYPHER


@pytest.mark.asyncio
async def test_a_name_only_filter_reads_no_values() -> None:
    execute = _Recorder()
    graph = NamedEntityGraph(execute, "arango")
    assert await graph.values_in_records("org", NamedEntityQuery(name_prefix="acme", kinds=["organization"]), ["r1"], 10) == {}
    assert await graph.values_in_records("org", NamedEntityQuery(amount_min=1, amount_max=2), [], 10) == {}
    assert await graph.values_in_records("org", None, ["r1"], 10) == {}
    assert execute.calls == []
