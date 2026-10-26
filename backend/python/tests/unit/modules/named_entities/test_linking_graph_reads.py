"""The named-entity reads behind search_by_entities: what each sends, per dialect.
Their results against real backends are in the graph contract tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.named_entities.graph_ops import (
    ENTITIES_BY_IDS_AQL,
    KEY_PREFIX_AQL,
    KEY_PREFIX_CYPHER,
    MENTIONS_IN_RECORDS_CYPHER,
    NamedEntityGraph,
)


class _Recorder:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows = rows or []

    async def __call__(self, statement: str, binds: dict[str, Any]) -> list[Any]:
        self.calls.append((statement, binds))
        return self.rows


@pytest.mark.asyncio
async def test_empty_inputs_send_nothing() -> None:
    execute = _Recorder()
    graph = NamedEntityGraph(execute, "neo4j")
    assert await graph.by_ids("org", []) == []
    assert await graph.by_key_prefix("org", [], limit=21) == []
    assert await graph.mentions_in_records("org", [], ["r"]) == []
    assert await graph.mentions_in_records("org", ["e"], []) == []
    assert execute.calls == []


@pytest.mark.asyncio
async def test_a_prefix_is_bounded_to_the_words_after_the_key() -> None:
    execute = _Recorder()
    await NamedEntityGraph(execute, "arango").by_key_prefix(
        "org", [("person", "name:person:sam"), ("person", "name:person:sam")], limit=0,
    )
    ((statement, binds),) = execute.calls
    assert statement == KEY_PREFIX_AQL
    assert binds["limit"] == 1
    assert binds["prefixes"] == [{
        "kind": "person", "key": "name:person:sam", "low": "name:person:sam ", "high": "name:person:sam ￿",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "statement"), [("arango", KEY_PREFIX_AQL), ("neo4j", KEY_PREFIX_CYPHER)])
async def test_each_dialect_gets_its_own_statement(dialect: str, statement: str) -> None:
    execute = _Recorder()
    await NamedEntityGraph(execute, dialect).by_key_prefix("org", [("person", "name:person:sam")], limit=5)
    assert execute.calls[0][0] == statement


@pytest.mark.asyncio
async def test_ids_and_records_are_deduplicated_and_rows_unwrapped() -> None:
    execute = _Recorder(rows=[{"n": {"id": "e1"}}, {"id": "e2"}])
    graph = NamedEntityGraph(execute, "arango")
    assert await graph.by_ids("org", ["e1", "e1", "e2"]) == [{"id": "e1"}, {"id": "e2"}]
    assert execute.calls[0] == (ENTITIES_BY_IDS_AQL, {"orgId": "org", "ids": ["e1", "e2"]})

    execute = _Recorder()
    await NamedEntityGraph(execute, "neo4j").mentions_in_records("org", ["e", "e"], ["r1", "r1", "r2"])
    assert execute.calls[0] == (
        MENTIONS_IN_RECORDS_CYPHER, {"orgId": "org", "entityIds": ["e"], "recordIds": ["r1", "r2"]},
    )
