"""The edge builder's per-repo block scan has an index on both backends (SC-3).

The scan filters on (orgId, recordGroupId) and pages by key. Without an index
ending in the key, every page re-reads and sorts the repo's whole index range.
"""
from unittest.mock import MagicMock

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def test_neo4j_declares_an_index_in_the_scans_order() -> None:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    statements = " ".join(provider._generate_performance_indexes())
    assert "FOR (n:Block) ON (n.orgId, n.recordGroupId, n.id)" in statements
