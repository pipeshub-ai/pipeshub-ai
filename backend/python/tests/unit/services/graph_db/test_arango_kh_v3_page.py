"""Arango's knowledge-hub v3 page orders and resumes in Python, so the order must
be the merge comparator's (``kh_merge.SortKey``) exactly: the partition merge
re-uses each page's own sort keys and cursor boundaries. Whether the rows are
the right ones is tested against Neo4j's pages in
``tests/integration/graph_permissions/test_provider_v3_listing_parity.py``."""

import bisect
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.localKB.handlers.kh_merge import key_for
from app.services.graph_db.arango.arango_http_provider import (
    ArangoHTTPProvider,
    _kh_v3_key,
    _kh_v3_ordered,
)


def _slim(seed: int, values: list) -> list[list]:
    rng = random.Random(seed)
    return [[f"records/r{rng.randrange(10_000):05d}-{i}", "record", rng.choice(values)] for i in range(300)]


VALUE_SETS = {
    "names": ["alpha", "Beta", "beta", "_x", "é", "zeta", "", None,
              "\uff5afull", "\U0001f600emoji", "\U00020000extb", "\ue000private"],
    "numbers": [0, 1, 5, 10, 10, 250, None],
}


@pytest.mark.parametrize("values", sorted(VALUE_SETS))
@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("seed", range(5))
def test_the_order_is_the_merge_comparators(values, descending, seed) -> None:
    slim = _slim(seed, VALUE_SETS[values])
    by_comparator = sorted(
        slim, key=lambda r: key_for({"id": r[0].split("/", 1)[1], "sortKey": r[2],
                                     "nullRank": 1 if r[2] is None else 0}, descending=descending),
    )
    assert [r[1] for r in _kh_v3_ordered(slim, descending=descending)] == [r[0] for r in by_comparator]


@pytest.mark.parametrize("descending", [False, True])
def test_resuming_by_bisection_is_filtering_by_the_comparator(descending) -> None:
    ordered = _kh_v3_ordered(_slim(7, VALUE_SETS["names"]), descending=descending)
    for row in ordered[::17]:
        boundary = _kh_v3_key(row, descending=descending)
        after = ordered[bisect.bisect_right(ordered, boundary, key=lambda r: _kh_v3_key(r, descending=descending)):]
        before = ordered[:bisect.bisect_left(ordered, boundary, key=lambda r: _kh_v3_key(r, descending=descending))]
        assert after == [r for r in ordered if boundary < _kh_v3_key(r, descending=descending)]
        assert before == [r for r in ordered if _kh_v3_key(r, descending=descending) < boundary]


@pytest.mark.asyncio
async def test_an_app_outside_the_gate_is_an_empty_page_without_a_query() -> None:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock()
    page = await provider.get_knowledge_hub_connector_page_v3(
        "app-2", "org-1", ["u"], ["app-1"], include_scope=True,
    )
    assert page["rows"] == [] and page["total"] == 0 and page["scope"] == {"admitted": False, "nodeId": "app-2"}
    provider.http_client.execute_aql.assert_not_awaited()


def test_no_arango_query_asks_for_the_plan_cache() -> None:
    """Storing some plans in Arango 3.12.4's plan cache crashes the server
    (SIGSEGV in SubqueryEndNode::estimateCost): the listing's slim query with the
    containers filter did, on its first request."""
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[4]
    source = (backend / "app/services/graph_db/arango/arango_http_provider.py").read_text(encoding="utf-8")
    assert "usePlanCache" not in source


@pytest.mark.asyncio
async def test_a_bounded_flatten_keeps_only_nodes_within_the_depth_whichever_arm_lists_them() -> None:
    """depth=2 from the App: the declared, seed and collection arms start elsewhere,
    so the bound on the region walk is not enough. A node two arms reach (r1) is kept
    once; one only a deep arm reaches (deep, s1, dg2) is dropped."""
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()

    async def walk(starts, step, bind, depth, transaction, within=None) -> list[str]:
        if step == "step_hier":
            if depth == 2:
                # m1: placed under the declared group dg, which the walk from the App does not enter.
                return ["recordGroups/g1", "records/r1", "recordGroups/dg", "records/m1"]
            return ["recordGroups/g1", "recordGroups/dg"]
        return ["recordGroups/g1", "records/r1"] if step == "step_app" else ["records/deep", "records/r1"]

    async def run(name, bind, transaction) -> list:
        return {
            "declared": ["recordGroups/dg"],
            "group_scope": [["recordGroups/dg", False], ["recordGroups/dg2", False]],
            "seeds": ["records/s1"],
        }[name]

    provider._kh_v3_walk = walk
    provider._kh_v3_run = run
    visible = await provider._kh_v3_flatten(
        {"handle": "apps/a1", "opens": True}, {"app_id": "a1", "granted": {}}, ["s1"], None, 2,
    )
    assert visible.nodes == ["recordGroups/g1", "records/r1", "recordGroups/dg"]
    assert visible.member_groups == ["recordGroups/dg"]
    assert visible.collection is True
    # Members are listed only when the hierarchy does not place them, so belonging
    # to a near group never admits a deep record.
    assert visible.unplaced_members_only is True
    # A placed record within the depth is listed if it belongs to any declared group.
    assert visible.placed == ["records/m1"]
    assert visible.placed_groups == ["recordGroups/dg", "recordGroups/dg2"]
