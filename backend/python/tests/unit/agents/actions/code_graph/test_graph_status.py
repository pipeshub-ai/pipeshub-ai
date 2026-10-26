"""A result read from a repo whose edges are not current says so (O-3)."""
import pytest

from app.agents.actions.code_graph.results import attach_graph_status, touched
from app.modules.code_graph.build_state import sync_point_key_for
from tests.unit.modules.code_graph.sync_point_fakes import FakeSyncPoints

pytestmark = pytest.mark.asyncio


async def _names(groups: list[str]) -> dict[str, str]:
    return {g: f"acme/{g}" for g in groups}


def _graph(**repos: dict) -> FakeSyncPoints:
    points = FakeSyncPoints()
    for group, fields in repos.items():
        points.points[sync_point_key_for(group)] = {
            "syncPointKey": sync_point_key_for(group), "orgId": "org", **fields,
        }
    return points


async def test_a_ready_repo_adds_nothing() -> None:
    result = touched({"neighbors": []}, [{"recordGroupId": "shop"}])
    graph = _graph(shop={"lastEdgeBuildAt": 5, "edgeBuildStatus": "ready"})
    assert await attach_graph_status(result, graph, "org", _names) == {"neighbors": []}


@pytest.mark.parametrize(("fields", "status"), [
    ({}, "never_built"),
    ({"lastEdgeBuildAt": 5, "edgeBuildPending": True}, "stale"),
    ({"lastEdgeBuildAt": 5, "edgeBuildStatus": "failed"}, "failed"),
    ({"edgeBuildStatus": "building"}, "building"),
])
async def test_an_empty_walk_says_why_it_may_be_empty(fields, status) -> None:
    result = touched({"neighbors": []}, [{"recordGroupId": "shop"}, None])
    graph = _graph(shop=fields) if fields else FakeSyncPoints()
    out = await attach_graph_status(result, graph, "org", _names)
    assert out["graph_status"] == {"acme/shop": status}
    assert "not proof" in out["graph_note"]
    assert "_graph_groups" not in out


async def test_only_the_repos_the_result_touched_are_reported() -> None:
    result = touched({"found": False}, [{"recordGroupId": "shop"}])
    graph = _graph(shop={"lastEdgeBuildAt": 5}, admin={"edgeBuildStatus": "failed"})
    assert "graph_status" not in await attach_graph_status(result, graph, "org", _names)


async def test_errors_and_unmarked_results_pass_through() -> None:
    assert await attach_graph_status({"error": "x"}, FakeSyncPoints(), "org", _names) == {
        "error": "x"}
    assert await attach_graph_status("text", FakeSyncPoints(), "org", _names) == "text"
