"""A connector's org-wide grants skip a user it syncs as external.

SHAREPOINT-01 / ONEDRIVE-01 / LINEAR-01: an org-wide grant ("Everyone except
external users", an organization sharing link, a public Linear team) stands for
the source tenant's members. A tenant guest is a PipesHub org member, so before
this the grant reached them. The guest's gate edge to the connector now says
``isExternalUser: true``, and every query that resolves the user's grants through
their organization skips that connector's org grants for them.

These read the statements each provider sends; the parity suite in
``tests/integration/graph_permissions/test_external_and_inactive_users.py``
runs them against both databases.
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


_NEO4J_ORG_OF_USER = re.compile(r"\(u\)-\[:BELONGS_TO\]->\((\w+):Organization\)")
_NEO4J_EXTERNAL_HERE = (
    "WHERE NOT EXISTS { (u)-[:USER_APP_RELATION {isExternalUser: true}]->(:App {id: $connector_id}) }"
)


def _neo4j_org_hops_without_the_external_rule(query: str) -> list[str]:
    flat = _flat(query)
    return [
        m.group(0) for m in _NEO4J_ORG_OF_USER.finditer(flat)
        if not flat[m.end():].lstrip().startswith(_NEO4J_EXTERNAL_HERE)
    ]


@pytest.mark.asyncio
async def test_neo4j_connector_grants_skip_the_org_for_an_external_user() -> None:
    recorder = _Recorder()
    provider = _neo4j(recorder)

    await provider.get_knowledge_hub_connector_grants("user-1", "org-1", "conn-1")
    await provider._kh_v3_connector_grantees("user-1", "conn-1", None)

    assert len(recorder.calls) == 2
    for query, params in recorder.calls:
        assert params["connector_id"] == "conn-1"
        assert _NEO4J_ORG_OF_USER.search(_flat(query)), "the query no longer reads the user's organization"
        assert not _neo4j_org_hops_without_the_external_rule(query)


@pytest.mark.asyncio
async def test_neo4j_access_skips_org_grants_of_connectors_the_user_is_external_to() -> None:
    recorder = _Recorder()
    provider = _neo4j(recorder)

    await provider.get_knowledge_hub_access_v3("user-1", "org-1")

    query = _flat(recorder.calls[0][0])
    externals = re.search(
        r"OPTIONAL MATCH \(u\)-\[:USER_APP_RELATION \{isExternalUser: true\}\]->\((\w+):App\) "
        r"WITH u, granteeIds, gatedApps, collect\(DISTINCT \1\.id\) AS (\w+)",
        query,
    )
    assert externals, "the user's external connectors are not collected"
    grant = query.index("OPTIONAL MATCH (grantee)-[kh_ge:PERMISSION]->(granted:Record|RecordGroup)")
    rule = f"AND NOT (grantee:Organization AND granted.connectorId IN {externals.group(2)})"
    assert rule in query[grant:grant + 400]


@pytest.mark.asyncio
async def test_arango_access_skips_org_grants_of_connectors_the_user_is_external_to() -> None:
    recorder = _Recorder()
    provider = _arango(recorder)

    await provider.get_knowledge_hub_access_v3("user-1", "org-1")

    query = _flat(recorder.calls[0][0])
    externals = re.search(
        r"LET (\w+) = \( FOR (\w+) IN userAppRelation "
        r"FILTER \2\._from == u\._id AND \2\.isExternalUser == true RETURN PARSE_IDENTIFIER\(\2\._to\)\.key \)",
        query,
    )
    assert externals, "the user's external connectors are not collected"
    grants = query.index("LET grants = (")
    rule = f'FILTER NOT (STARTS_WITH(g, "organizations/") AND granted.connectorId IN {externals.group(1)})'
    assert rule in query[grants:grants + 900]


# R1-07: the search prefilter. check_access re-decides every hit, so nothing
# leaked, but a guest's vector filter held every org-wide record of the
# connector, which crowded the top-k and was dropped afterwards.


@pytest.mark.asyncio
async def test_neo4j_search_prefilter_skips_the_org_for_an_external_user() -> None:
    recorder = _Recorder()
    provider = _neo4j(recorder)

    await provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1", None, raise_on_error=True)

    query, params = recorder.calls[0]
    flat = _flat(query)
    assert params["connector_id"] == params["connectorId"] == "conn-1"
    external = re.search(
        r"WITH u AS caller, \$connectorId IN gatedApps AS appGranted, "
        r"EXISTS \{ \(u\)-\[:USER_APP_RELATION \{isExternalUser: true\}\]->\(:App \{id: \$connector_id\}\) \} "
        r"AS (\w+)",
        flat,
    )
    assert external, "the caller's external flag for this connector is not read"
    org_arm = flat.index("MATCH (userDoc)-[:BELONGS_TO]->(o:Organization)-[orgPerm:PERMISSION]->(granted)")
    assert f"WHERE orgPerm.type IN $orgShareTypes AND NOT {external.group(1)}" in flat[org_arm:org_arm + 200]


@pytest.mark.asyncio
async def test_arango_search_prefilter_skips_the_org_for_an_external_user() -> None:
    recorder = _Recorder()
    provider = _arango(recorder)

    await provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1", None, raise_on_error=True)

    flat = _flat(recorder.calls[0][0])
    external = re.search(
        r"LET (\w+) = LENGTH\( FOR (\w+) IN userAppRelation "
        r"FILTER \2\._from == userDoc\._id AND \2\.isExternalUser == true "
        r'AND \2\._to == CONCAT\("apps/", @connectorId\) LIMIT 1 RETURN 1 \) > 0',
        flat,
    )
    assert external, "the caller's external flag for this connector is not read"
    assert f"LET orgGrants = {external.group(1)} ? [] : (" in flat
