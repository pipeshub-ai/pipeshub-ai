"""A `record_id` resolves to a path only for a record the caller may read."""
import pytest

from app.agents.actions.code_graph import code_graph as cg

from .conftest import CONN, ORG, USER, FakeGraphProvider


def _tool(graph: FakeGraphProvider) -> cg.CodeGraph:
    return cg.CodeGraph({
        "org_id": ORG, "user_id": USER, "graph_provider": graph, "blob_store": object(),
    })


@pytest.mark.asyncio
async def test_a_readable_record_resolves_to_its_path() -> None:
    graph = FakeGraphProvider()
    graph.records["rec-b"]["recordGroupId"] = "repo-1"
    path, repo, error = await _tool(graph)._anchor_path(CONN, None, "rec-b")
    # The record names its repo, so a path two repos share still resolves to it.
    assert (path, repo, error) == ("src/b.py", "repo-1", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("graph", "connector_id"),
    [(FakeGraphProvider(deny_records={"rec-b"}), CONN), (FakeGraphProvider(), "other-conn")],
    ids=["unreadable", "another connector"],
)
async def test_a_hidden_record_answers_like_an_unknown_one(
    graph: FakeGraphProvider, connector_id: str
) -> None:
    tool = _tool(graph)
    path, _, error = await tool._anchor_path(connector_id, None, "rec-b")
    _, _, unknown = await tool._anchor_path(connector_id, None, "rec-missing")

    assert path is None
    assert "src/b.py" not in str(error)
    assert error == {"error": unknown["error"].replace("rec-missing", "rec-b")}
