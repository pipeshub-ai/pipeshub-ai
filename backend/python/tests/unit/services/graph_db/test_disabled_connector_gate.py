"""A disabled connector (App ``isActive`` false) is closed at the one gate on
both backends; a KB has no enable switch and stays open. Behaviour is covered
in tests/integration/graph_permissions/test_disabled_connector.py."""

from __future__ import annotations

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def test_neo4j_gate_closes_disabled_connectors() -> None:
    cypher = Neo4jProvider._kh_gate_cypher()
    assert "gatedApp.orgId = $org_id AND (gatedApp.type = 'KB' OR coalesce(gatedApp.isActive, true))" in cypher
    assert "kh_linkedApp.orgId = $org_id AND (kh_linkedApp.type = 'KB' OR coalesce(kh_linkedApp.isActive, true))" in cypher


def test_arango_gate_closes_disabled_connectors() -> None:
    aql = ArangoHTTPProvider._kh_gate_aql()
    assert 'gated.orgId == @org_id AND (gated.type == "KB" OR gated.isActive != false)' in aql
    assert 'linked.orgId == @org_id AND (linked.type == "KB" OR linked.isActive != false)' in aql
