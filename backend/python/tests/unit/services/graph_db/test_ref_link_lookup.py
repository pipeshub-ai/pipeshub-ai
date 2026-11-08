"""The storage-reference link lookup of both stores: its shape, and the one index it relies on."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import REF_LINKS_PER_DOC
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def neo4j() -> Neo4jProvider:
    p = Neo4jProvider.__new__(Neo4jProvider)
    p.logger = MagicMock()
    p.client = MagicMock()
    return p


def test_neo4j_ships_a_single_property_externalrecordid_index_that_is_idempotent() -> None:
    """The composite (externalRecordId, connectorId) index is used only when both are in the predicate."""
    statements = neo4j()._generate_performance_indexes()
    mine = [s for s in statements if "record_external_record_id" in s]
    assert len(mine) == 1 and "IF NOT EXISTS" in mine[0] and "ON (n.externalRecordId)" in mine[0]


async def test_neo4j_link_lookup_caps_per_document_and_reads_both_link_kinds() -> None:
    p = neo4j()
    p.client.execute_query = AsyncMock(side_effect=[[{"docId": "d1", "recordId": "r1", "kind": "externalRecordId", "origin": "UPLOAD", "connectorName": "KB", "connectorId": "kb"}],
                                                      [{"docId": "d2", "recordId": "f1", "kind": "files.path", "origin": None, "connectorName": None, "connectorId": None}]])
    rows = await p._ref_link_rows(["d1", "d2"])
    assert [r["kind"] for r in rows] == ["externalRecordId", "files.path"]
    first, second = p.client.execute_query.await_args_list
    assert first.kwargs["parameters"]["ids"] == ["d1", "d2"] and first.kwargs["parameters"]["per_doc"] == REF_LINKS_PER_DOC
    assert second.kwargs["parameters"]["paths"] == ["storage://d1", "storage://d2"]
    # The cap sits inside a per-document subquery: a call-wide LIMIT lets one document's links crowd out another's only link.
    for query in (first.args[0], second.args[0]):
        assert "UNWIND" in query and "CALL (" in query and "$limit" not in query
    assert "substring(path, 10)" in second.args[0]


async def test_arango_link_lookup_caps_per_document_and_reads_both_link_kinds() -> None:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.execute_query = AsyncMock(side_effect=[[{"docId": "d1", "recordId": "r1", "kind": "externalRecordId"}], []])
    rows = await p._ref_link_rows(["d1"])
    assert rows == [{"docId": "d1", "recordId": "r1", "kind": "externalRecordId"}]
    first, second = p.execute_query.await_args_list
    assert first.kwargs["bind_vars"]["ids"] == ["d1"] and first.kwargs["bind_vars"]["per_doc"] == REF_LINKS_PER_DOC
    assert second.kwargs["bind_vars"]["paths"] == ["storage://d1"] and "SUBSTRING(path, 10)" in second.args[0]
    assert "FOR doc IN @ids" in first.args[0] and "LIMIT @per_doc" in first.args[0] and "@limit" not in first.args[0]
    assert "FOR path IN @paths" in second.args[0] and "LIMIT 1" in second.args[0] and "@limit" not in second.args[0]
