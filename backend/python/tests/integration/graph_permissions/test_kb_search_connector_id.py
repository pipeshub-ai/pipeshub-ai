"""A search scoped to a knowledge base finds the knowledge base's own files: the
connector filter reads each node's own connectorId, which for a collection file
is the collection's id (upstream 8ce888517, fixed there in the removed v1 search;
the v3 listing must keep it)."""

from __future__ import annotations

import pytest

from .fixture_graph import _node, app, bt, nr, perm, rec
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-kbs"
USER = "kbs-u"
KB = "kbs-kb"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    item = dict(record_type="FILE", origin="UPLOAD", connector_id=KB)
    nodes = [
        _org(_node("User", USER, userId="kbs-m", email="kbs@example.com", fullName="U")),
        _org(app(KB, "HR", connector="KB", app_group="Local Storage", scope="personal")),
        _org(rec("kbs-doc", "budget 2026", **item)),
        _org(rec("kbs-other", "holidays", **item)),
    ]
    edges = [
        perm(USER, KB, role="OWNER"),
        nr(KB, "kbs-doc"), bt("kbs-doc", KB, "KB"),
        nr(KB, "kbs-other"), bt("kbs-other", KB, "KB"),
    ]
    return nodes, edges


@pytest.fixture(params=["neo4j", "arango"])
async def provider(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    nodes, edges = _graph()
    if request.param == "neo4j":
        await neo4j_provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'kbs-' DETACH DELETE n")
        await load_into_neo4j(neo4j_settings, nodes, edges)
        yield neo4j_provider
        await neo4j_provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'kbs-' DETACH DELETE n")
    else:
        await load_into_arango(arango_settings, nodes, edges)
        yield arango_provider
        for c in ("permission", "belongsTo", "nodeRelations"):
            await arango_provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/kbs-') OR CONTAINS(e._to, '/kbs-') REMOVE e IN @@c", {"@c": c})
        for c in ("apps", "records", "users"):
            await arango_provider.http_client.execute_aql(
                "FOR d IN @@c FILTER STARTS_WITH(d._key, 'kbs-') REMOVE d IN @@c", {"@c": c})


async def test_a_search_scoped_to_a_collection_finds_its_files(provider) -> None:
    access = await provider.get_knowledge_hub_access_v3(USER, ORG)
    page = await provider.get_knowledge_hub_connector_page_v3(
        app_id=KB, org_id=ORG, grantee_ids=access["grantee_ids"], gated_app_ids=access["gated_app_ids"],
        grants_by_connector=access["by_connector"], limit=50, flatten=True, sort_field="name",
        sort_dir="ASC", include_total=True,
        filters={"search_query": "budget", "connector_ids": [KB]},
    )
    assert [row["id"] for row in page["rows"]] == ["kbs-doc"]
    assert page["rows"][0]["connectorId"] == KB
