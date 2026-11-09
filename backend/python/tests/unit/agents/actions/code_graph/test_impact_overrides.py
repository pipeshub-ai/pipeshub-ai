"""impact on an implementation reaches the callers that go through its interface.

A call typed to an interface or base class resolves to the declared method; the implementation only OVERRIDES it.
An inbound walk from the implementation therefore never met those callers. impact now steps through the methods an
anchor overrides (outbound OVERRIDES, transitively): a stepping stone is walked at the same hop, not reported, and
a caller found through one says which declared method it went through (``via_override``). A base the caller may
not read stops the walk there, and get_neighbour is unchanged.
"""
import json

import pytest

from app.agents.actions.code_graph import code_graph as cg
from app.agents.actions.code_graph.neighbours import get_neighbour_impl, impact_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import BLOCKS, CONN, ORG, USER, QueryGraphProvider, _block

pytestmark = pytest.mark.asyncio

PORTS, STRIPE, SVC, API = "src/ports.py", "src/stripe.py", "src/svc.py", "src/api.py"
SYMBOLS = {  # key: (record, path, kind, name)
    "k_port": ("rec-p", PORTS, "method", "Port.charge"),
    "k_base": ("rec-p", PORTS, "method", "Base.charge"),
    "k_impl": ("rec-i", STRIPE, "method", "Stripe.charge"),
    "k_settle": ("rec-s", SVC, "method", "Svc.settle"),
    "k_handle": ("rec-h", API, "function", "handle"),
}


def edge(a: str, b: str, rel: str) -> dict:
    return {"_from": f"{BLOCKS}/{a}", "_to": f"{BLOCKS}/{b}", "relationshipType": rel}


def graph(*edges: dict, deny=(), provider: type[QueryGraphProvider] = QueryGraphProvider) -> QueryGraphProvider:
    g = provider(deny_records=deny)
    for key, (rid, path, kind, name) in SYMBOLS.items():
        block = _block(key, rid, path, key, name, kind)
        block.pop("filePath")
        g.blocks[key] = block
        g.code_files[rid] = path
        g.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-{rid}"}
    g.edges = list(edges)
    return g


INTERFACE = (edge("k_impl", "k_port", "OVERRIDES"), edge("k_settle", "k_port", "CALLS"))


async def _impact(g: QueryGraphProvider, **kwargs) -> dict:
    return without_marker(await impact_impl(
        graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path=STRIPE, qualified_name="method:Stripe.charge", **kwargs))


def _files(out: dict) -> dict[str, dict]:
    return {f["file_path"]: f for f in out["impacted_files"]}


async def test_impact_on_an_implementation_reaches_the_caller_typed_to_its_interface() -> None:
    files = _files(await _impact(graph(*INTERFACE)))
    assert files[SVC]["symbols"] == ["method:Svc.settle"] and files[SVC]["nearest_hop"] == 1
    assert files[SVC]["via_override"] == {"method:Svc.settle": f"{PORTS}#method:Port.charge"}


async def test_the_stepping_stone_itself_is_walked_not_reported() -> None:
    out = await _impact(graph(*INTERFACE))
    assert PORTS not in _files(out)
    assert out["impacted_symbols"] == 1


async def test_overrides_are_followed_transitively() -> None:
    g = graph(edge("k_impl", "k_port", "OVERRIDES"), edge("k_port", "k_base", "OVERRIDES"),
              edge("k_settle", "k_base", "CALLS"))
    files = _files(await _impact(g))
    assert files[SVC]["via_override"] == {"method:Svc.settle": f"{PORTS}#method:Base.charge"}


async def test_a_base_the_caller_cannot_read_stops_the_walk_and_is_never_named() -> None:
    out = await _impact(graph(*INTERFACE, deny=["rec-p"]))
    assert out["impacted_files"] == []
    assert PORTS not in repr(out) and "Port.charge" not in repr(out)


async def test_an_override_cycle_ends() -> None:
    g = graph(edge("k_impl", "k_port", "OVERRIDES"), edge("k_port", "k_base", "OVERRIDES"),
              edge("k_base", "k_port", "OVERRIDES"), edge("k_settle", "k_port", "CALLS"))
    files = _files(await _impact(g))
    assert files[SVC]["symbols"] == ["method:Svc.settle"]


async def test_a_base_that_a_real_edge_reaches_is_reported_once_as_that_edge() -> None:
    """A template method on the base that calls the implementation is a real caller."""
    g = graph(*INTERFACE, edge("k_port", "k_impl", "CALLS"))
    files = _files(await _impact(g))
    assert files[PORTS]["symbols"] == ["method:Port.charge"] and "via_override" not in files[PORTS]
    assert files[SVC]["via_override"] == {"method:Svc.settle": f"{PORTS}#method:Port.charge"}


async def test_callers_of_the_interface_caller_are_the_next_hop() -> None:
    g = graph(*INTERFACE, edge("k_handle", "k_settle", "CALLS"))
    files = _files(await _impact(g))
    assert files[API]["nearest_hop"] == 2 and "via_override" not in files[API]
    assert files[SVC]["nearest_hop"] == 1


async def test_calls_only_impact_steps_through_overrides_too() -> None:
    """The stepping stone is how the call reaches the implementation, not an edge type the caller asked for."""
    files = _files(await _impact(graph(*INTERFACE), edge_types=["CALLS"]))
    assert files[SVC]["symbols"] == ["method:Svc.settle"]


class Counting(QueryGraphProvider):
    """Records the keys of every OVERRIDES lookup."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.override_lookups: list[list[str]] = []

    async def get_neighbors_for_nodes_by_relationship_types(self, **kwargs) -> list[dict]:
        if kwargs.get("relationship_types") == ["OVERRIDES"]:
            self.override_lookups.append(sorted(kwargs["node_keys"]))
        return await super().get_neighbors_for_nodes_by_relationship_types(**kwargs)


def counting(*edges: dict) -> Counting:
    return graph(*edges, provider=Counting)


async def test_a_plain_function_impact_makes_no_overrides_lookup() -> None:
    """Only a method or constructor can override (symbol_index's method index): a function frontier skips the
    lookup."""
    g = counting(edge("k_handle", "k_settle", "CALLS"))
    await impact_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, file_path=API,
                      qualified_name="function:handle", depth=3)
    assert g.override_lookups == []


async def test_only_the_methods_of_a_frontier_are_looked_up_and_a_later_hop_still_steps() -> None:
    """handle (a function) calls Stripe.charge, which overrides Port.charge: hop 2's frontier holds the method."""
    g = counting(edge("k_handle", "k_impl", "CALLS"), *INTERFACE)
    g.edges.append(edge("k_impl", "k_handle", "CALLS"))  # gives handle a caller, so hop 2 starts from it
    await impact_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, file_path=API,
                      qualified_name="function:handle", depth=2)
    assert g.override_lookups == [["k_impl"], ["k_port"]], "hop 1's function is skipped; the bases' closure is not"
    files = _files(await _impact(graph(*INTERFACE)))
    assert files[SVC]["via_override"]  # a method anchor still steps through its interface


async def test_get_neighbour_is_unchanged() -> None:
    out = await get_neighbour_impl(graph_provider=graph(*INTERFACE), connector_id=CONN, org_id=ORG, user_id=USER,
                                   file_path=STRIPE, qualified_name="method:Stripe.charge", direction="inbound")
    assert out["neighbors"] == []


async def test_the_impact_tool_lists_an_interface_caller_once_in_impacted_files(monkeypatch) -> None:
    """The walk reaches callers through the overridden method, so the tool adds no second section for them."""
    async def search(**_kwargs) -> dict:
        return {"matches": []}

    monkeypatch.setattr(cg, "search_code_impl", search)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph(*INTERFACE), "blob_store": object()})
    ok, raw = await tool.impact(CONN, file_path=STRIPE, qualified_name="method:Stripe.charge")
    out = json.loads(raw)
    assert ok and "through_overridden_methods" not in out
    assert [f["file_path"] for f in out["impacted_files"]] == [SVC]
    assert out["impacted_files"][0]["via_override"] == {"method:Svc.settle": f"{PORTS}#method:Port.charge"}
