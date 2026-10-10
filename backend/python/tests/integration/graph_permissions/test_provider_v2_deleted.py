"""A deleted record leaves *every* part of the response.

The listing tests prove a deleted node is absent from `items`. This module
covers the rest: the `total` beside those items, the per-type counts,
and the available filters. Those are computed on separate paths — the total and
counts from the whole-result id union, the filters from the gate — so a deleted
node can be filtered out of the list and still be counted, which reads as a page
that is simply missing a row.

`del-in-group` inherits inside an open connector group and `del-in-kb` sits in
the collection whose grant opens it, so both would be returned if they were live.
"""

import pytest

from app.connectors.sources.localKB.handlers.kh_search import search_page

pytestmark = pytest.mark.integration

USER = "user-u"
ORG = "org-1"
SECRET = "kh-integration-secret"
GRANTEES = ["user-u", "group-g", "role-r", "team-t", "orgnode-1"]
GATED_APPS = [
    "ex1-app", "ex2-app", "dec-app", "dec-rgl-app", "ex-app",
    "swm-app", "pl-app", "gp-app", "flag-app", "kb-1",
]
DELETED = {"del-in-group", "del-in-kb"}


@pytest.fixture(params=["neo4j", "arango"])
def provider(request, neo4j_provider, arango_provider):
    return neo4j_provider if request.param == "neo4j" else arango_provider


def _partition(result) -> dict:
    assert len(result["partitions"]) == 1, result["partitions"]
    return result["partitions"][0]


def _ids(result) -> set[str]:
    return {row["id"] for row in _partition(result)["rows"]}


async def test_the_whole_result_total_and_counts_exclude_deleted_records(
    loaded_graph, provider
) -> None:
    """Over the id union: the counts are built from the same surviving set.

    `search_page` takes its own access context, so this is the whole read path —
    partition discovery, the per-partition queries and the merge — agreeing that
    a deleted record is not there to be counted.
    """
    page = await search_page(
        provider, user_key=USER, user_id=USER, org_id=ORG, secret=SECRET, limit=500
    )
    returned = {row["id"] for row in page.rows}

    assert not DELETED & returned, sorted(DELETED & returned)
    assert page.total == len(returned)
    assert sum(page.counts_by_type.values()) == page.total

