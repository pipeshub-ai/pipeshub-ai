"""get_neighbour outbound names the implementations of a base or interface method it calls.

A call typed to an interface resolves to the declared method; the code that runs is an implementation, which
only OVERRIDES it. An outbound walk now adds, beside such a callee, the methods that override it (inbound
OVERRIDES, transitively), readable only, ranked by whether their class is constructed in the repo (RTA), then
by degree, then by path, and capped at 8 per callee with the rest counted. find_symbol_path walks every readable implementation
outbound, uncapped.
"""
import pytest

from app.agents.actions.code_graph.neighbours import (
    get_neighbour_impl,
    impact_impl,
    walk_neighbours,
)
from app.agents.actions.code_graph.path import find_symbol_path_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import CONN, ORG, USER, _block
from .test_impact_overrides import INTERFACE, PORTS, STRIPE, SVC, edge, graph

pytestmark = pytest.mark.asyncio


async def _out(g, *, qualified_name: str = "method:Svc.settle", file_path: str = SVC, **kw) -> dict:
    return without_marker(await get_neighbour_impl(
        graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, file_path=file_path,
        qualified_name=qualified_name, direction="outbound", **kw))


def _add(g, key: str, rid: str, path: str, kind: str, name: str, parent: str | None = None) -> None:
    block = _block(key, rid, path, key, name, kind)
    block.pop("filePath")
    if parent:
        block["parentBlockId"] = parent
    g.blocks[key] = block
    g.code_files[rid] = path
    g.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-{rid}"}


def _by_name(out: dict) -> dict[str, dict]:
    return {n["qualified_name"]: n for n in out["neighbors"]}


async def test_an_outbound_walk_names_the_implementation_of_the_interface_method_it_calls() -> None:
    out = await _out(graph(*INTERFACE))
    rows = _by_name(out)
    assert rows["method:Port.charge"]["relation"] == "CALLS" and "via" not in rows["method:Port.charge"]
    impl = rows["method:Stripe.charge"]
    assert impl["file_path"] == STRIPE and impl["via"] == "implementation"
    assert impl["of"] == f"{PORTS}#method:Port.charge" and impl["instantiated"] is False
    assert "implementations_omitted" not in out


async def test_implementations_of_implementations_are_named_too() -> None:
    g = graph(edge("k_impl", "k_port", "OVERRIDES"), edge("k_port", "k_base", "OVERRIDES"),
              edge("k_settle", "k_base", "CALLS"))
    rows = _by_name(await _out(g))
    assert rows["method:Port.charge"]["of"] == rows["method:Stripe.charge"]["of"] == f"{PORTS}#method:Base.charge"


async def test_constructed_classes_rank_first_and_more_than_eight_are_counted() -> None:
    g = graph(edge("k_settle", "k_port", "CALLS"))
    for i in range(10):
        _add(g, f"k_cls{i}", f"rec-{i}", f"src/impl{i}.py", "class", f"Impl{i}")
        _add(g, f"k_m{i}", f"rec-{i}", f"src/impl{i}.py", "method", f"Impl{i}.charge", parent=f"k_cls{i}")
        g.edges.append(edge(f"k_m{i}", "k_port", "OVERRIDES"))
    g.edges.append(edge("k_handle", "k_cls7", "CALLS"))  # Impl7 is constructed somewhere: RTA says it can run
    out = await _out(g)
    impls = [n for n in out["neighbors"] if n.get("via") == "implementation"]
    assert len(impls) == 8 and out["implementations_omitted"] == 2
    assert [n["qualified_name"] for n in impls if n["instantiated"]] == ["method:Impl7.charge"]
    ranked = sorted(impls, key=lambda n: (not n["instantiated"], n["file_path"]))
    assert ranked[0]["qualified_name"] == "method:Impl7.charge"
    # Unconstructed ones fill the rest by path; the last two by path are the ones left out.
    assert {n["qualified_name"] for n in impls} == {"method:Impl7.charge", *(f"method:Impl{i}.charge" for i in range(7))}


async def test_an_implementation_in_a_file_the_caller_cannot_read_is_never_shown_or_counted() -> None:
    g = graph(*INTERFACE, deny=["rec-i"])
    out = await _out(g)
    assert "method:Stripe.charge" not in _by_name(out) and STRIPE not in repr(out)
    assert "implementations_omitted" not in out


async def test_a_hidden_constructor_call_does_not_rank_an_implementation() -> None:
    """Whether a class is constructed must not depend on a file the caller cannot read."""
    g = graph(edge("k_settle", "k_port", "CALLS"))
    _add(g, "k_cls", "rec-c1", "src/a_impl.py", "class", "AImpl")
    _add(g, "k_m", "rec-c1", "src/a_impl.py", "method", "AImpl.charge", parent="k_cls")
    _add(g, "k_new", "rec-hidden", "src/secret.py", "function", "build")
    g.edges += [edge("k_m", "k_port", "OVERRIDES"), edge("k_new", "k_cls", "CALLS")]
    g.deny_records.add("rec-hidden")
    rows = _by_name(await _out(g))
    assert rows["method:AImpl.charge"]["instantiated"] is False


async def test_a_direct_callee_is_not_repeated_as_an_implementation() -> None:
    g = graph(*INTERFACE, edge("k_settle", "k_impl", "CALLS"))
    impl = [n for n in (await _out(g))["neighbors"] if n["qualified_name"] == "method:Stripe.charge"]
    assert len(impl) == 1 and "via" not in impl[0]


async def test_what_an_implementation_calls_is_the_next_hop() -> None:
    g = graph(*INTERFACE, edge("k_impl", "k_handle", "CALLS"))
    rows = _by_name(await _out(g, depth=2))
    assert rows["function:handle"]["hop"] == 2
    assert rows["method:Stripe.charge"]["hop"] == 1 and rows["method:Stripe.charge"]["via"] == "implementation"


async def test_an_implementation_past_hop_one_keeps_the_hop_path_as_via() -> None:
    """Every row past hop 1 says which node it was reached through (`via`); an implementation there too,
    and `of` alone marks it as one."""
    g = graph(*INTERFACE, edge("k_handle", "k_settle", "CALLS"))
    rows = _by_name(await _out(g, qualified_name="function:handle", file_path="src/api.py", depth=2))
    impl = rows["method:Stripe.charge"]
    assert impl["hop"] == 2 and impl["via"] == f"{SVC}#method:Svc.settle"
    assert impl["of"] == f"{PORTS}#method:Port.charge" and impl["instantiated"] is False
    assert rows["method:Port.charge"]["via"] == f"{SVC}#method:Svc.settle"


async def test_inbound_walks_and_impact_add_no_implementation_rows() -> None:
    g = graph(*INTERFACE, edge("k_handle", "k_port", "CALLS"))
    inbound = without_marker(await get_neighbour_impl(
        graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, file_path=PORTS,
        qualified_name="method:Port.charge", direction="inbound"))
    assert not [n for n in inbound["neighbors"] if n.get("via") == "implementation"]
    impact = without_marker(await impact_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER,
                                              file_path=STRIPE, qualified_name="method:Stripe.charge"))
    assert "implementation" not in repr(impact)



async def test_the_shared_walk_returns_only_stored_edges_unless_implementations_are_asked_for() -> None:
    """The graph read API pages walk_neighbours' rows by cursor: an implementation row is no stored edge."""
    g = graph(*INTERFACE)

    async def walk(**kw: bool) -> object:
        return await walk_neighbours(g, ORG, USER, [dict(g.blocks["k_settle"])], relations=["CALLS"],
                                     direction="outbound", connector_id=CONN, **kw)

    plain = await walk()
    assert [r["key"] for r in plain.rows] == ["k_port"] and plain.implementations_omitted == 0
    asked = await walk(implementations=True)
    assert sorted(r["key"] for r in asked.rows) == ["k_impl", "k_port"]

# find_symbol_path follows the same implementations on a directed walk.

async def _path(g, a: tuple[str, str], b: tuple[str, str], **kw) -> dict:
    return without_marker(await find_symbol_path_impl(
        graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, file_path_a=a[0], qualified_name_a=a[1],
        file_path_b=b[0], qualified_name_b=b[1], **kw))


SETTLE, HANDLE = (SVC, "method:Svc.settle"), ("src/api.py", "function:handle")


async def test_a_directed_path_runs_through_the_implementation_of_the_called_interface_method() -> None:
    out = await _path(graph(*INTERFACE, edge("k_impl", "k_handle", "CALLS")), SETTLE, HANDLE)
    assert out["found"] is True and "reversed" not in out and "capped" not in out
    first, second = out["hops"]
    assert first["to"]["qualified_name"] == "method:Stripe.charge" and first["via"] == "implementation"
    assert first["relation"] == "CALLS" and first["explain"] == (
        "method:Svc.settle calls method:Port.charge, implemented by method:Stripe.charge")
    assert second["to"]["qualified_name"] == "function:handle" and "via" not in second


async def test_a_path_never_walks_an_implementation_the_caller_cannot_read() -> None:
    out = await _path(graph(*INTERFACE, edge("k_impl", "k_handle", "CALLS"), deny=["rec-i"]), SETTLE, HANDLE)
    assert out["found"] is False and STRIPE not in repr(out)


def _ten_implementations(deny=()) -> object:
    g = graph(edge("k_settle", "k_port", "CALLS"), deny=deny)
    for i in range(10):
        _add(g, f"k_cls{i}", f"rec-{i}", f"src/impl{i}.py", "class", f"Impl{i}")
        _add(g, f"k_m{i}", f"rec-{i}", f"src/impl{i}.py", "method", f"Impl{i}.charge", parent=f"k_cls{i}")
        g.edges.append(edge(f"k_m{i}", "k_port", "OVERRIDES"))
    return g


async def test_a_path_walks_every_implementation_past_get_neighbours_cap() -> None:
    # get_neighbour names 8; a search needs them all, so a missing path is never "capped" by that list.
    out = await _path(_ten_implementations(), SETTLE, ("src/impl9.py", "method:Impl9.charge"))
    assert out["found"] is True and "capped" not in out


async def test_hidden_implementations_are_neither_walked_nor_reported() -> None:
    out = await _path(_ten_implementations(deny=["rec-8", "rec-9"]), SETTLE, HANDLE)
    assert out["found"] is False and "capped" not in out and "Impl8" not in repr(out)


async def test_a_hidden_base_callee_is_no_way_to_its_implementations() -> None:
    out = await _path(graph(*INTERFACE, edge("k_impl", "k_handle", "CALLS"), deny=["rec-p"]), SETTLE, HANDLE)
    assert out["found"] is False and "Port" not in repr(out)


async def test_a_hidden_middle_override_level_stops_the_walk_there() -> None:
    g = graph(edge("k_settle", "k_b0", "CALLS"), edge("k_m1", "k_b0", "OVERRIDES"),
              edge("k_m2", "k_m1", "OVERRIDES"), edge("k_m2", "k_handle", "CALLS"), deny=["rec-m1"])
    for key, rid, name in (("k_b0", "rec-b0", "B0"), ("k_m1", "rec-m1", "M1"), ("k_m2", "rec-m2", "M2")):
        _add(g, key, rid, f"src/{rid[4:]}.py", "method", f"{name}.charge")
    out = await _path(g, SETTLE, HANDLE)
    assert out["found"] is False and "M1" not in repr(out) and "M2" not in repr(out)


async def test_a_path_without_overrides_or_undirected_is_unchanged() -> None:
    plain = await _path(graph(edge("k_settle", "k_handle", "CALLS")), SETTLE, HANDLE)
    assert plain["hops"][0]["explain"] == "method:Svc.settle calls function:handle" and "via" not in plain["hops"][0]
    undirected = await _path(graph(*INTERFACE), SETTLE, (STRIPE, "method:Stripe.charge"), undirected=True)
    assert [h["relation"] for h in undirected["hops"]] == ["CALLS", "OVERRIDES"]
    assert not [h for h in undirected["hops"] if h.get("via")]


async def test_a_call_to_a_method_reached_earlier_through_overrides_still_leads_to_its_implementations() -> None:
    """settle -> Sib.charge -OVERRIDES-> Port.charge visits Port.charge at hop 2; the call to it at hop 3
    (settle -> b -> c -> Port.charge) must still step on to Stripe.charge, which only that call reaches."""
    g = graph(edge("k_settle", "k_sib", "CALLS"), edge("k_sib", "k_port", "OVERRIDES"),
              edge("k_settle", "k_b", "CALLS"), edge("k_b", "k_c", "CALLS"), edge("k_c", "k_port", "CALLS"),
              edge("k_impl", "k_port", "OVERRIDES"))
    _add(g, "k_sib", "rec-sib", "src/sib.py", "method", "Sib.charge")
    _add(g, "k_b", "rec-b", "src/b.py", "function", "b")
    _add(g, "k_c", "rec-c", "src/c.py", "function", "c")
    out = await _path(g, SETTLE, (STRIPE, "method:Stripe.charge"))
    assert out["found"] is True and "reversed" not in out
    assert [h["to"]["qualified_name"] for h in out["hops"]] == ["function:b", "function:c", "method:Stripe.charge"]
    assert out["hops"][-1]["via"] == "implementation"
