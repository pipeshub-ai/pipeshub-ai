"""GATE-NEVER-REMOVED: a user deleted from PipesHub passes no connector gate.

Deleting a PipesHub user only sets ``isActive`` false: their edges stay, the All
team's among them, so the gate let them through to every org-wide grant. These
read the statements each provider sends; the parity suite in
``tests/integration/graph_permissions/test_external_and_inactive_users.py`` runs
them against both databases.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, query: str, *args: object, **kwargs: object) -> list:
        params = kwargs.get("parameters") or kwargs.get("bind_vars") or (args[0] if args else {}) or {}
        self.calls.append((query, dict(params)))
        return []


def _neo4j(recorder: _Recorder) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=None)
    provider.client = MagicMock()
    provider.client.execute_query = recorder
    return provider


def _arango(recorder: _Recorder) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = recorder
    return provider


def _flat(query: str) -> str:
    return re.sub(r"\s+", " ", query)


def test_neo4j_gate_lets_no_inactive_user_through() -> None:
    gate = _flat(Neo4jProvider._kh_gate_cypher())
    assert gate.lstrip().startswith("WITH u WHERE coalesce(u.isActive, true)")
    carried = _flat(Neo4jProvider._kh_gate_cypher(org="gateOrg", carry="gateOrg"))
    assert carried.lstrip().startswith("WITH u, gateOrg WHERE coalesce(u.isActive, true)")


def test_arango_gate_lets_no_inactive_user_through() -> None:
    gate = _flat(ArangoHTTPProvider._kh_gate_aql())
    assert gate.lstrip().startswith("FILTER u.isActive != false")


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_every_gated_question_carries_the_inactive_rule(make) -> None:
    recorder = _Recorder()
    provider = make(recorder)

    await provider.get_knowledge_hub_access_context_v2("user-1", "org-1")
    await provider.get_knowledge_hub_access_v3("user-1", "org-1")
    await provider.get_gated_apps("user-1", "org-1")

    assert len(recorder.calls) == 3
    for query, _ in recorder.calls:
        flat = _flat(query)
        assert "coalesce(u.isActive, true)" in flat or "FILTER u.isActive != false" in flat
