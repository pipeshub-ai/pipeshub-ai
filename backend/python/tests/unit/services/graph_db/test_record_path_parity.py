"""Arango and Neo4j must build the same ancestor path for a record or group.

Both providers return candidate chains and share ``select_canonical_chain_names``.
The fixtures are the anomalies that used to make the backends disagree:
duplicate edges, several canonical parents, equal-length ties, cycles.
"""

import inspect
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango(rows=None, error=None):
    provider = object.__new__(ArangoHTTPProvider)
    provider.http_client = MagicMock(execute_aql=AsyncMock(return_value=rows, side_effect=error))
    provider.logger = MagicMock()
    return provider


def _neo4j(rows=None, error=None):
    provider = object.__new__(Neo4jProvider)
    provider.client = MagicMock(execute_query=AsyncMock(return_value=rows, side_effect=error))
    provider.logger = MagicMock()
    return provider


RECORD_CASES = {
    "single_chain_with_prefix_rows": (
        [
            {"ids": ["f"], "names": ["f.txt"]},
            {"ids": ["f", "d"], "names": ["f.txt", "Docs"]},
            {"ids": ["f", "d", "r"], "names": ["f.txt", "Docs", "Root"]},
        ],
        ["Root", "Docs", "f.txt"],
    ),
    "duplicate_parent_edges": (
        [
            {"ids": ["f", "d"], "names": ["f.txt", "Docs"]},
            {"ids": ["f", "d"], "names": ["f.txt", "Docs"]},
        ],
        ["Docs", "f.txt"],
    ),
    "two_parents_longest_wins": (
        [
            {"ids": ["f", "a"], "names": ["f", "A"]},
            {"ids": ["f", "b", "br"], "names": ["f", "B", "BRoot"]},
        ],
        ["BRoot", "B", "f"],
    ),
    "two_parents_tie_smallest_ids": (
        [
            {"ids": ["f", "p2"], "names": ["f", "Two"]},
            {"ids": ["f", "p1"], "names": ["f", "One"]},
        ],
        ["One", "f"],
    ),
    # f -> a -> b -> a: both backends stop before revisiting a, so only these rows come back.
    "cycle_rows_without_repeated_vertex": (
        [
            {"ids": ["f"], "names": ["f"]},
            {"ids": ["f", "a"], "names": ["f", "A"]},
            {"ids": ["f", "a", "b"], "names": ["f", "A", "B"]},
        ],
        ["B", "A", "f"],
    ),
    "empty_names_dropped": ([{"ids": ["f", "d"], "names": ["f", ""]}], ["f"]),
    "unnamed_vertex_still_counts_for_length": (
        [
            {"ids": ["f", "a", "ar"], "names": ["f", None, "ARoot"]},
            {"ids": ["f", "b"], "names": ["f", "B"]},
        ],
        ["ARoot", "f"],
    ),
    "record_missing": ([], []),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True], ids=["as_returned", "reversed"])
@pytest.mark.parametrize("rows,expected", RECORD_CASES.values(), ids=RECORD_CASES.keys())
async def test_record_path_segments_parity(rows, expected, reverse):
    rows = list(reversed(rows)) if reverse else rows
    arango = await _arango(rows).get_record_path_segments("f")
    neo4j = await _neo4j(rows).get_record_path_segments("f")
    assert arango == neo4j == expected


@pytest.mark.asyncio
async def test_arango_record_query_walks_only_record_relations_with_prune():
    provider = _arango([])
    await provider.get_record_path_segments("f", transaction="tx-1")
    call = provider.http_client.execute_aql.await_args
    query = call.args[0]
    assert "GRAPH" not in query
    assert "INBOUND start_record recordRelations" in query
    assert "PRUNE" in query
    assert 'uniqueVertices: "path"' in query
    # AQL grammar: PRUNE must precede OPTIONS or the query fails to parse.
    assert query.index("PRUNE") < query.index("OPTIONS")
    # Secondary key keeps the LIMIT subset identical to Neo4j's on ties.
    assert "SORT LENGTH(p.vertices) DESC, p.vertices[*]._key ASC" in query
    assert "externalParentId != null" in query
    assert call.kwargs["bind_vars"] == {
        "record_id": "f",
        "records_collection": "records",
        "relation_types": ["PARENT_CHILD", "ATTACHMENT"],
        "max_candidates": 64,
    }
    assert call.kwargs["txn_id"] == "tx-1"


@pytest.mark.asyncio
async def test_neo4j_record_query_prunes_inside_the_quantified_pattern():
    provider = _neo4j([])
    await provider.get_record_path_segments("f", transaction="tx-1")
    call = provider.client.execute_query.await_args
    query = call.args[0]
    assert "){0,100}" in query
    assert "RECORD_RELATION*" not in query
    assert "ORDER BY size(path_nodes) DESC, [n IN path_nodes | n.id] ASC" in query
    assert call.kwargs["parameters"] == {
        "record_id": "f",
        "relation_types": ["PARENT_CHILD", "ATTACHMENT"],
        "max_candidates": 64,
    }
    assert call.kwargs["txn_id"] == "tx-1"


@pytest.mark.asyncio
async def test_neo4j_record_query_excludes_paths_that_repeat_a_vertex():
    # Arango gets this from uniqueVertices: "path"; Cypher's default match mode
    # only keeps relationships distinct, so the query must drop repeats itself.
    provider = _neo4j([])
    await provider.get_record_path_segments("f")
    query = " ".join(provider.client.execute_query.await_args.args[0].split())
    assert "NOT path_nodes[i] IN path_nodes[i + 1..]" in query
    assert query.index("NOT path_nodes[i] IN") < query.index("ORDER BY")


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_record_path_error_default_returns_empty(make):
    provider = make(error=RuntimeError("down"))
    assert await provider.get_record_path_segments("f") == []
    provider.logger.error.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_record_path_error_raises_when_asked(make):
    with pytest.raises(RuntimeError):
        await make(error=RuntimeError("down")).get_record_path_segments("f", raise_on_error=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_record_missing_with_raise_on_error_returns_empty(make):
    assert await make([]).get_record_path_segments("f", raise_on_error=True) == []


GROUP_CASES = {
    "root_only": ([{"ids": ["g"], "groupNames": ["G"], "names": [None]}], ["G"]),
    "nested": (
        [
            {"ids": ["g"], "groupNames": ["G"], "names": [None]},
            {"ids": ["g", "p"], "groupNames": ["G", "P"], "names": [None, None]},
        ],
        ["P", "G"],
    ),
    "two_parents_tie_smallest_ids": (
        [
            {"ids": ["g", "pb"], "groupNames": ["G", "B"], "names": [None, None]},
            {"ids": ["g", "pa"], "groupNames": ["G", "A"], "names": [None, None]},
        ],
        ["A", "G"],
    ),
    "empty_group_name_falls_back_to_name": (
        [{"ids": ["g", "p"], "groupNames": ["G", ""], "names": [None, "Parent"]}],
        ["Parent", "G"],
    ),
    # g -> a -> b -> a: both backends stop before revisiting a, so only these rows come back.
    "cycle_rows_without_repeated_vertex": (
        [
            {"ids": ["g"], "groupNames": ["G"], "names": [None]},
            {"ids": ["g", "a"], "groupNames": ["G", "A"], "names": [None, None]},
            {"ids": ["g", "a", "b"], "groupNames": ["G", "A", "B"], "names": [None, None, None]},
        ],
        ["B", "A", "G"],
    ),
    "group_missing": ([], []),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True], ids=["as_returned", "reversed"])
@pytest.mark.parametrize("rows,expected", GROUP_CASES.values(), ids=GROUP_CASES.keys())
async def test_record_group_path_parity(rows, expected, reverse):
    rows = list(reversed(rows)) if reverse else rows
    arango = await _arango(rows).get_record_group_path("g")
    neo4j = await _neo4j(rows).get_record_group_path("g")
    assert arango == neo4j == expected


@pytest.mark.asyncio
async def test_arango_group_query_is_cycle_safe_and_belongs_to_only():
    provider = _arango([])
    await provider.get_record_group_path("g", transaction="tx-1")
    call = provider.http_client.execute_aql.await_args
    query = call.args[0]
    assert "GRAPH" not in query
    assert "OUTBOUND start_rg belongsTo" in query
    assert 'uniqueVertices: "path"' in query
    assert "PRUNE" in query
    # AQL grammar: PRUNE must precede OPTIONS or the query fails to parse.
    assert query.index("PRUNE") < query.index("OPTIONS")
    assert "SORT LENGTH(p.vertices) DESC, p.vertices[*]._key ASC" in query
    assert call.kwargs["bind_vars"] == {
        "record_group_id": "g",
        "rg_collection": "recordGroups",
        "max_candidates": 64,
    }
    assert call.kwargs["txn_id"] == "tx-1"


@pytest.mark.asyncio
async def test_neo4j_group_query_only_crosses_record_groups():
    provider = _neo4j([])
    await provider.get_record_group_path("g", transaction="tx-1")
    call = provider.client.execute_query.await_args
    query = call.args[0]
    assert "((:RecordGroup)-[:BELONGS_TO]->(:RecordGroup)){0,50}" in query
    assert "BELONGS_TO*" not in query
    assert "ORDER BY size(path_nodes) DESC, [n IN path_nodes | n.id] ASC" in query
    assert call.kwargs["parameters"] == {
        "record_group_id": "g",
        "max_candidates": 64,
    }
    assert call.kwargs["txn_id"] == "tx-1"


@pytest.mark.asyncio
async def test_neo4j_group_query_excludes_paths_that_repeat_a_vertex():
    provider = _neo4j([])
    await provider.get_record_group_path("g")
    query = " ".join(provider.client.execute_query.await_args.args[0].split())
    assert "NOT path_nodes[i] IN path_nodes[i + 1..]" in query
    assert query.index("NOT path_nodes[i] IN") < query.index("ORDER BY")


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_group_path_error_default_returns_empty(make):
    provider = make(error=RuntimeError("down"))
    assert await provider.get_record_group_path("g") == []
    provider.logger.error.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_group_path_error_raises_when_asked(make):
    with pytest.raises(RuntimeError):
        await make(error=RuntimeError("down")).get_record_group_path("g", raise_on_error=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
async def test_group_missing_with_raise_on_error_returns_empty(make):
    assert await make([]).get_record_group_path("g", raise_on_error=True) == []


def _squash(text: str) -> str:
    return " ".join(text.split())


async def _arango_query(method: str, arg: str) -> str:
    provider = _arango([])
    await getattr(provider, method)(arg)
    return provider.http_client.execute_aql.await_args.args[0]


@pytest.mark.asyncio
async def test_arango_record_query_filters_exactly_what_it_prunes():
    # PRUNE still emits the vertex it stops at; the FILTER must repeat the same
    # step condition, or an edit to one silently changes which chains are walked.
    query = await _arango_query("get_record_path_segments", "f")
    pruned = re.search(r"PRUNE e != null AND NOT \((.*?)\)\s*OPTIONS", query, re.S)
    kept = re.search(r"FILTER e == null OR \((.*?)\)\s*SORT", query, re.S)
    assert pruned and kept
    assert _squash(pruned.group(1)) == _squash(kept.group(1))
    assert "e.relationshipType IN @relation_types" in pruned.group(1)


@pytest.mark.asyncio
async def test_arango_group_query_filters_exactly_what_it_prunes():
    query = await _arango_query("get_record_group_path", "g")
    pruned = re.search(r"PRUNE NOT (.*?)\s*OPTIONS", query, re.S)
    kept = re.search(r"FILTER (.*?)\s*SORT", query.split("OPTIONS", 1)[1], re.S)
    assert pruned and kept
    assert _squash(pruned.group(1)) == _squash(kept.group(1))


def _graph_providers() -> list[type]:
    found, pending = [], list(IGraphDBProvider.__subclasses__())
    while pending:
        cls = pending.pop()
        found.append(cls)
        pending.extend(cls.__subclasses__())
    # Only backends from the application: test doubles other test modules build
    # linger in __subclasses__() until they are garbage-collected.
    return [cls for cls in found if not inspect.isabstract(cls) and cls.__module__.startswith("app.")]


@pytest.mark.parametrize("method", ["get_record_path_segments", "get_record_group_path"])
def test_every_provider_accepts_raise_on_error(method):
    # Storage paths and pattern-match scoping always pass it; a backend without
    # it would fail every lookup with a TypeError.
    providers = _graph_providers()
    assert {ArangoHTTPProvider, Neo4jProvider} <= set(providers)
    for cls in providers:
        params = inspect.signature(getattr(cls, method)).parameters
        assert "raise_on_error" in params, f"{cls.__name__}.{method}"
        assert params["raise_on_error"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["raise_on_error"].default is False
