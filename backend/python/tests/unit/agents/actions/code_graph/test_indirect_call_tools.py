"""INDIRECT_CALL in the tools.

impact follows it backwards ("who registers this handler"), get_neighbour walks it by default, and
entry_points keeps counting CALLS only: a view named in a URL table is still an entry point.
"""
import pytest

from app.agents.actions.code_graph.neighbours import (
    IMPACT_RELATIONS,
    get_neighbour_impl,
    impact_impl,
)
from app.agents.actions.code_graph.ops import CODE_RELATIONS
from app.agents.actions.code_graph.path import _HOP_VERBS
from app.agents.actions.code_graph.query import entry_points_impl
from app.agents.actions.code_graph.results import without_marker
from app.config.constants.arangodb import RecordRelations

from .conftest import BLOCKS, CONN, ORG, USER, QueryGraphProvider, _block

pytestmark = pytest.mark.asyncio

INDIRECT = RecordRelations.INDIRECT_CALL.value
ROUTES, VIEWS = "src/routes.py", "src/views.py"
SYMBOLS = {  # key: (record, path, kind, name)
    "k_view": ("rec-v", VIEWS, "function", "show"),
    "k_dispatch": ("rec-r", ROUTES, "function", "dispatch"),
}


def graph(*edges: dict) -> QueryGraphProvider:
    g = QueryGraphProvider()
    for key, (rid, path, kind, name) in SYMBOLS.items():
        block = _block(key, rid, path, key, name, kind)
        block.pop("filePath")
        g.blocks[key] = block
        g.code_files[rid] = path
        g.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-{rid}",
                          "recordType": "CODE_FILE"}
    g.edges = list(edges)
    return g


REGISTERED = {"_from": f"{BLOCKS}/k_dispatch", "_to": f"{BLOCKS}/k_view", "relationshipType": INDIRECT,
              "confidence": "INFERRED", "context": "dispatch"}


def test_the_relation_is_in_every_walk_list() -> None:
    assert INDIRECT in IMPACT_RELATIONS
    assert INDIRECT in CODE_RELATIONS
    assert INDIRECT in _HOP_VERBS


async def test_impact_reaches_whoever_registers_the_handler() -> None:
    out = without_marker(await impact_impl(
        graph_provider=graph(REGISTERED), connector_id=CONN, org_id=ORG, user_id=USER,
        file_path=VIEWS, qualified_name="function:show"))
    files = {f["file_path"]: f for f in out["impacted_files"]}
    assert files[ROUTES]["symbols"] == ["function:dispatch"]


async def test_get_neighbour_walks_it_by_default() -> None:
    out = without_marker(await get_neighbour_impl(
        graph_provider=graph(REGISTERED), connector_id=CONN, org_id=ORG, user_id=USER,
        file_path=ROUTES, qualified_name="function:dispatch", direction="outbound"))
    rows = [n for n in out["neighbors"] if n.get("qualified_name") == "function:show"]
    assert rows and rows[0]["relation"] == INDIRECT


async def test_an_indirectly_called_function_is_still_an_entry_point() -> None:
    out = await entry_points_impl(graph_provider=graph(REGISTERED), connector_id=CONN, org_id=ORG, user_id=USER)
    assert "function:show" in {p["qualified_name"] for p in out["entry_points"]}
