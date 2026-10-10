"""Backward paging and whole-result ids against both real engines.

Global search needs two things from every partition query: a previous page
that is exactly the page the user came from, and, on the first page, every
matching id so a node found in two partitions counts once.
"""

import pytest

pytestmark = pytest.mark.integration

USER = "user-u"
ORG = "org-1"
GRANTEES = ["user-u", "group-g", "role-r", "team-t", "orgnode-1"]
GATED_APPS = [
    "ex1-app", "ex2-app", "dec-app", "dec-rgl-app", "ex-app",
    "swm-app", "pl-app", "gp-app", "flag-app", "kb-1",
]


@pytest.fixture(params=["neo4j", "arango"])
def provider(request, neo4j_provider, arango_provider):
    return neo4j_provider if request.param == "neo4j" else arango_provider


def _root(prov):
    async def call(**kwargs):
        return await prov.get_knowledge_hub_root_nodes_v2(
            user_key=USER, org_id=ORG, user_app_ids=GATED_APPS, **kwargs,
        )
    return call


def _boundary(row: dict) -> dict:
    return {"nullRank": row["nullRank"], "sortKey": row["sortKey"], "id": row["id"]}


async def _pages_forward(call, limit, **kwargs) -> list[list[dict]]:
    pages, after = [], None
    for _ in range(100):
        part = (await call(limit=limit, after=after, **kwargs))["partitions"][0]
        pages.append(part["rows"])
        if not part["hasMore"]:
            return pages
        after = _boundary(part["rows"][-1])
    raise AssertionError("forward paging did not terminate")


async def test_the_root_listing_applies_search_filters(loaded_graph, provider) -> None:
    """The Apps partition of a global search takes the same filters as the others."""
    part = (await _root(provider)(limit=50, search_query="PLACEMENT"))["partitions"][0]
    assert {r["id"] for r in part["rows"]} == {"pl-app"}



async def test_the_root_orders_names_by_utf16_unit(loaded_graph, provider) -> None:
    """Arango sorted the root in AQL, by collation ("éclair" before "zeta"); every
    other listing, and Neo4j's root, orders by UTF-16 code unit."""
    names = {"rootsort-1": "éclair-kb", "rootsort-2": "zeta-kb", "rootsort-3": "_u-kb", "rootsort-4": "Eagle-kb"}
    await provider.batch_upsert_nodes(
        [{"_key": k, "id": k, "orgId": ORG, "name": n, "type": "KB", "appGroup": "Local Storage",
          "scope": "personal", "isActive": True, "createdAtTimestamp": 1} for k, n in names.items()],
        collection="apps",
    )
    try:
        async def root(**kwargs):
            return await provider.get_knowledge_hub_root_nodes_v2(
                user_key=USER, org_id=ORG, user_app_ids=list(names), sort_field="name", **kwargs)

        part = (await root(limit=50, sort_dir="ASC"))["partitions"][0]
        assert [r["name"] for r in part["rows"]] == ["_u-kb", "Eagle-kb", "zeta-kb", "éclair-kb"]
        pages, after = [], None
        for _ in range(10):
            p = (await root(limit=1, sort_dir="DESC", after=after))["partitions"][0]
            pages += [r["name"] for r in p["rows"]]
            if not p["hasMore"]:
                break
            after = _boundary(p["rows"][-1])
        assert pages == ["éclair-kb", "zeta-kb", "Eagle-kb", "_u-kb"]
        back = (await root(limit=2, sort_dir="DESC", after=after, direction="prev"))["partitions"][0]
        assert [r["name"] for r in back["rows"]] == ["éclair-kb", "zeta-kb"]
    finally:
        await provider.delete_nodes(list(names), "apps")
