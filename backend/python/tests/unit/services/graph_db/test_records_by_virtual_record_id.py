"""`get_records_by_virtual_record_id` — the authority for vector deletion.

A point is removed only when this returns nothing, so its two properties are
load-bearing in opposite directions:

- it must NOT be scoped by connector or org, or a deduplicated sibling would go
  unseen and its vectors would be deleted with the connector that shared them;
- it MUST exclude soft-deleted records, or a tombstone answers "still
  referenced" and those vectors are never reclaimed.

Both backends are checked because the delete path reaches this through
``IGraphDBProvider`` and a divergence would mean one deployment quietly deletes
differently from the other.
"""

import re

import pytest

from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

QUERY_SOURCES = {
    "arango": (
        "app/services/graph_db/arango/arango_http_provider.py",
        "get_records_by_virtual_record_id",
    ),
    "neo4j": (
        "app/services/graph_db/neo4j/neo4j_provider.py",
        "get_records_by_virtual_record_id",
    ),
}


def _method_source(path: str, name: str) -> str:
    """The text of one method, for asserting on the query it builds.

    These providers compose query strings rather than exposing them, and both
    need a live database to execute. Reading the source is what lets the two
    backends be held to the same predicate without one.
    """
    import pathlib

    text = pathlib.Path(path).read_text()
    start = text.index(f"async def {name}(")
    # Next sibling method at the same indentation ends it.
    rest = text[start:]
    match = re.search(r"\n    (?:async )?def ", rest[10:])
    return rest[: match.start() + 10] if match else rest


@pytest.fixture(params=sorted(QUERY_SOURCES), ids=sorted(QUERY_SOURCES))
def source(request) -> str:
    path, name = QUERY_SOURCES[request.param]
    return _method_source(path, name)


class TestExcludesSoftDeletedRecords:
    def test_the_query_filters_is_deleted(self, source):
        """A tombstone answering "still referenced" strands its vectors."""
        assert "isDeleted" in source

    def test_arango_uses_a_null_safe_comparison(self):
        src = _method_source(*QUERY_SOURCES["arango"])
        # AQL: `!= true` is already null-safe, so records predating the field pass.
        assert "isDeleted != true" in src

    def test_neo4j_uses_a_null_safe_comparison(self):
        """`<> true` is NOT null-safe in Cypher — `null <> true` is null, which
        WHERE treats as false, silently dropping every record that predates the
        field. coalesce is what keeps them."""
        src = _method_source(*QUERY_SOURCES["neo4j"])
        assert "coalesce(r.isDeleted, false) = false" in src
        assert "r.isDeleted <> true" not in src


class TestNotScopedByTenantOrConnector:
    """Narrowing this query would make deletion unsafe, not stricter."""

    def test_no_connector_filter(self, source):
        assert "connectorId" not in source

    def test_no_org_filter(self, source):
        assert "orgId" not in source


class TestInterfaceContract:
    def test_declared_abstract_on_the_interface(self):
        """Callers reach it through IGraphDBProvider; a provider that omitted it
        would break the delete path at runtime rather than at construction."""
        assert getattr(
            IGraphDBProvider.get_records_by_virtual_record_id,
            "__isabstractmethod__",
            False,
        )

    def test_both_providers_implement_it(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert "get_records_by_virtual_record_id" in vars(cls), cls.__name__

    def test_neither_provider_is_left_abstract(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert not getattr(cls, "__abstractmethods__", frozenset()), (
                f"{cls.__name__} is missing: {sorted(cls.__abstractmethods__)}"
            )
