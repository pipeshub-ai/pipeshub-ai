"""A connected app's limit trims after the walk has counted, so rows outside the limit still fill a page.

The limit check is replaced by a stand-in that names the record groups (repos) a limit leaves out; the
real check is a store query.
"""
import pytest

from app.api.routes import graph_view as routes
from tests.unit.agents.actions.code_graph.conftest import CONN
from tests.unit.modules.graph_view.world import Spec, World, expand

OUT = {"repo-out"}


def hub(outside: int, inside: int, *, outside_last: bool = False) -> World:
    """`hub` in repo-in with `outside` neighbours in repo-out (same connector) and `inside` neighbours in repo-in.
    The outside keys sort before the inside ones, or after them with `outside_last`."""
    out_prefix, in_prefix = ("z", "a") if outside_last else ("a", "z")
    records = {"r_hub": CONN, "r_out": CONN, "r_in": CONN}
    blocks = {"hub": "r_hub", **{f"{out_prefix}{i}": "r_out" for i in range(outside)},
              **{f"{in_prefix}{i}": "r_in" for i in range(inside)}}
    edges = [("hub", k, "CALLS") for k in blocks if k != "hub"]
    spec = Spec(records=records, blocks=blocks, edges=edges)
    spec.acl = {"u": set(records)}
    world = World(spec)
    for key, doc in world.blocks.items():
        doc["recordGroupId"] = "repo-out" if key.startswith(out_prefix) else "repo-in"
    return world


@pytest.fixture(autouse=True)
def stand_in_limit(monkeypatch) -> None:
    async def outside(request, graph, org, user, ids) -> set:
        return {i for i in ids if i in OUT}

    monkeypatch.setattr(routes, "ids_outside_connection", outside)
    monkeypatch.setattr(routes, "groups_outside_limit", outside)


def walk(world: World, limit: int) -> list:
    cursor, pages = None, []
    for _ in range(50):
        body = expand(world, "u", ["hub"], limit=limit, **({"cursor": cursor} if cursor else {})).json()
        pages.append(body)
        cursor = body["page"].get("nextCursor")
        if not cursor:
            return pages
    raise AssertionError("did not end")


ORDERS = pytest.mark.parametrize("outside_last", [False, True], ids=["outside-first", "outside-last"])


def _what_a_caller_sees(pages: list) -> list:
    return [(p["edges"], p["nodes"], p["anchors"], p["page"]) for p in pages]


@ORDERS
@pytest.mark.parametrize("limit", [1, 2, 3, 4])   # 3 neighbours: limit 3 ends the list exactly on a page
def test_rows_outside_the_limit_do_not_change_a_page(limit, outside_last) -> None:
    with_outside, without = walk(hub(5, 3, outside_last=outside_last), limit), walk(hub(0, 3, outside_last=outside_last), limit)
    assert _what_a_caller_sees(with_outside) == _what_a_caller_sees(without)


@ORDERS
@pytest.mark.parametrize("limit", [1, 2, 3, 4])
def test_every_neighbour_inside_the_limit_is_reachable(limit, outside_last) -> None:
    pages = walk(hub(5, 3, outside_last=outside_last), limit)
    reached = {e["to"] for p in pages for e in p["edges"]}
    inside = "a" if outside_last else "z"
    assert reached == {f"block:{inside}{i}" for i in range(3)}, f"reached {sorted(reached)} in {len(pages)} pages"
    assert not any(p["page"]["truncated"] and not p["edges"] for p in pages), "an empty page that says there is more"


@ORDERS
def test_the_last_page_of_a_limited_hub_is_not_truncated(outside_last) -> None:
    last = walk(hub(5, 3, outside_last=outside_last), 3)[-1]
    assert last["page"] == {"truncated": False, "incomplete": False} and len(last["edges"]) == 3
