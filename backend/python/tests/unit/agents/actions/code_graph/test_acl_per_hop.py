"""A file the caller cannot read is a wall, not a window, at every hop (S-7)."""
import pytest

from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.ops import RecordAccess, _degrees
from app.agents.actions.code_graph.path import find_symbol_path_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import BLOCKS, CONN, ORG, USER, FakeGraphProvider, _block

pytestmark = pytest.mark.asyncio


def _edge(a: str, b: str) -> dict:
    return {"_from": f"{BLOCKS}/{a}", "_to": f"{BLOCKS}/{b}", "relationshipType": "CALLS"}


def _graph_with_hidden_middle() -> FakeGraphProvider:
    """caller -> hidden -> renderPanel, where hidden lives in a file the user cannot read;
    and a longer readable route caller -> target -> fetchTarget -> renderPanel."""
    graph = FakeGraphProvider(deny_records=["rec-secret"])
    hidden = _block("k_hidden", "rec-secret", "secret/impl.py", "secret_impl", "impl")
    graph.code_files["rec-secret"] = hidden.pop("filePath")
    graph.blocks["k_hidden"] = hidden
    graph.records["rec-secret"] = {"_key": "rec-secret", "orgId": ORG, "connectorId": CONN}
    graph.edges += [
        _edge("k_caller", "k_hidden"), _edge("k_hidden", "k_panel"),
        _edge("k_target", "k_client"), _edge("k_client", "k_panel"),
    ]
    return graph


async def test_a_walk_does_not_continue_through_a_hidden_file() -> None:
    out = await get_neighbour_impl(
        graph_provider=_graph_with_hidden_middle(), connector_id=CONN, org_id=ORG,
        user_id=USER, file_path="src/a.py", qualified_name="function:caller",
        direction="outbound", edge_types=["CALLS"], depth=2,
    )
    names = {n["qualified_name"] for n in out["neighbors"]}
    assert "function:impl" not in names
    assert "function:renderPanel" not in names, "reached only through the hidden file"
    assert "function:fetchTarget" in names, "the readable second hop is still walked"


async def test_a_path_goes_around_a_hidden_file() -> None:
    """The shortest path runs through the hidden file; a longer readable one exists."""
    out = without_marker(await find_symbol_path_impl(
        graph_provider=_graph_with_hidden_middle(), connector_id=CONN, org_id=ORG,
        user_id=USER, file_path_a="src/a.py", qualified_name_a="function:caller",
        file_path_b="web/ui/panel.ts", qualified_name_b="function:renderPanel",
        edge_types=["CALLS"],
    ))
    assert out["found"] is True
    assert [h["to"]["qualified_name"] for h in out["hops"]] == [
        "function:target", "function:fetchTarget", "function:renderPanel"]


async def test_degree_counts_only_readable_neighbours() -> None:
    graph = _graph_with_hidden_middle()
    blocks = [graph.blocks["k_caller"], graph.blocks["k_target"]]
    counts, _ = await _degrees(graph, blocks, ["CALLS"], ORG, RecordAccess(graph, ORG, USER))
    # caller calls target and the hidden impl; only target counts.
    assert counts["k_caller"] == 1


async def test_a_scope_that_cannot_be_read_denies_instead_of_widening(monkeypatch) -> None:
    """An empty scope means every connector; failing to read one must not become that (S-6)."""
    from app.agents.actions.code_graph import code_graph as cg
    from app.agents.actions.knowledge_graph.ops import scope

    def broken(_state: object) -> None:
        raise RuntimeError("scope store down")

    monkeypatch.setattr(scope, "derive_scope", broken)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    assert "error" in tool._in_scope(CONN)


async def test_a_failure_reaches_the_model_as_an_id_not_its_text() -> None:
    """Exception text can name hosts, queries and paths (S-9)."""
    from app.agents.actions.code_graph import code_graph as cg

    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})

    async def call() -> dict:
        raise RuntimeError("connect to arangodb://10.0.0.7:8529 failed")

    out = await tool._run("get_neighbour", CONN, call, "Neighbour lookup failed")
    assert out["error"].startswith("Neighbour lookup failed (error id ")
    assert "10.0.0.7" not in out["error"]
