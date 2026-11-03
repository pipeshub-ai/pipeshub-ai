"""How far the per-connector search map reaches, in both graph providers.

``_get_virtual_ids_for_connector`` lists the records a filtered search may return;
``check_access`` then decides every hit. A record the check admits and the map leaves
out is lost without an error, so the map has to cover every chain the check follows:
inheritance as deep as the hierarchy, from a granted record as well as from a granted
record group, and from the App for a user who passes its gate.

Each test runs the real provider method against a stubbed driver and reads the query
it sent. Behaviour on a real graph is in tests/integration/graph_db/test_connector_search_access.py.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import ACCESS_WALK_MAX_DEPTH
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

_INHERIT = CollectionNames.INHERIT_PERMISSIONS.value
_PERMISSION = CollectionNames.PERMISSION.value
_RECORDS = CollectionNames.RECORDS.value
_GROUPS = CollectionNames.RECORD_GROUPS.value


class _Recorder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def __call__(self, query: str, *args: object, **kwargs: object) -> list:
        self.queries.append(query)
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
    provider.execute_query = recorder
    return provider


_MAKE = {"neo4j": _neo4j, "arango": _arango}
_INHERIT_WALK = {
    "neo4j": re.compile(r"INHERIT_PERMISSIONS\*(\d+)\.\.(\d+)"),
    "arango": re.compile(rf"IN (\d+)\.\.(\d+) INBOUND \w+ {_INHERIT}\b"),
}
_HIERARCHY_WALK = {
    "neo4j": re.compile(r"\)\{\d+,(\d+)\}"),
    "arango": re.compile(r"IN \d+\.\.(\d+) INBOUND"),
}


async def _map_query(backend: str) -> str:
    recorder = _Recorder()
    await _MAKE[backend](recorder)._get_virtual_ids_for_connector(
        "user-1", "org-1", "conn-1", raise_on_error=True,
    )
    (query,) = recorder.queries
    return query


async def _check_text(backend: str) -> str:
    """Every statement of the batch access check; ArangoDB runs it in stages."""
    if backend == "arango":
        return "\n".join(ArangoHTTPProvider._kh_v3_check_aql().values())
    recorder = _Recorder()
    await _neo4j(recorder).check_access("user-key-1", "org-1", node_ids=["rec-1"])
    return "\n".join(recorder.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["neo4j", "arango"])
async def test_inheritance_is_followed_as_deep_as_the_access_check_walks(backend: str) -> None:
    check_depth = max(int(d) for d in _HIERARCHY_WALK[backend].findall(await _check_text(backend)))

    walks = _INHERIT_WALK[backend].findall(await _map_query(backend))

    assert walks == [("0", str(ACCESS_WALK_MAX_DEPTH))], walks
    assert ACCESS_WALK_MAX_DEPTH >= check_depth


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["neo4j", "arango"])
async def test_every_grant_seeds_the_walk_whether_on_a_record_or_a_record_group(backend: str) -> None:
    query = await _map_query(backend)

    if backend == "neo4j":
        grants = re.findall(r"-\[\w*:PERMISSION\]->\((\w+)(:\w+)?\)\s+(?:WHERE|RETURN)", query)
        assert grants == [("granted", "")] * 4, grants
        assert "(granted:Record OR granted:RecordGroup) AND granted.connectorId = $connectorId" in query
        assert "MATCH (seed)<-[:INHERIT_PERMISSIONS*" in query
    else:
        grants = re.findall(rf"FOR (\w+)(?:, \w+)? IN 1\.\.1 ANY \w+(?:\._id)? {_PERMISSION}\s+(?:FILTER|RETURN)", query)
        assert grants.count("granted") == 4, grants
        seed_filter = (
            f'FILTER (IS_SAME_COLLECTION("{_RECORDS}", granted) OR IS_SAME_COLLECTION("{_GROUPS}", granted))'
            " AND granted.connectorId == @connectorId"
        )
        assert query.count(seed_filter) == 4
        assert re.search(rf"FOR record IN 0\.\.\d+ INBOUND seed {_INHERIT}\b", query)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["neo4j", "arango"])
async def test_a_team_grant_counts_as_in_the_access_check(backend: str) -> None:
    query = await _map_query(backend)

    if backend == "neo4j":
        assert "WHERE (g:Group OR g:Role OR g:Teams)" in query
    else:
        assert f'IS_SAME_COLLECTION("{CollectionNames.TEAMS.value}", group)' in query


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["neo4j", "arango"])
async def test_the_app_seeds_the_walk_only_for_a_user_who_passes_its_gate(backend: str) -> None:
    recorder = _Recorder()
    provider = _MAKE[backend](recorder)
    await provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1", raise_on_error=True)
    (query,) = recorder.queries

    if backend == "neo4j":
        assert provider._kh_gate_cypher(org="$orgId") in query
        assert "$connectorId IN gatedApps AS appGranted" in query
        assert re.search(r"MATCH \(app:App \{id: \$connectorId\}\)\s+WHERE appGranted", query)
    else:
        assert provider._kh_gate_aql(org="@orgId") in query
        apps = CollectionNames.APPS.value
        assert f'@connectorId IN kh_gated_apps ? [CONCAT("{apps}/", @connectorId)] : []' in query
