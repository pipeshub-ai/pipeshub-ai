"""Rows the caller cannot read must not change a walk or a degree count (and must not hide readable ones)."""
import pytest

from app.agents.actions.code_graph import neighbours as nb
from app.agents.actions.code_graph import ops
from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.ops import RecordAccess, _degrees

from .conftest import BLOCKS, CONN, ORG, RECORDS, USER, FakeGraphProvider, _block

LIMIT = 5      # edge rows a hop may keep, patched over _ROWS_PER_ANCHOR
BUDGET = 40    # raw rows a hop may scan, patched over _HOP_SCAN_LIMIT


@pytest.fixture(autouse=True)
def small_caps(monkeypatch) -> None:
    monkeypatch.setattr(nb, "_ROWS_PER_ANCHOR", LIMIT)
    monkeypatch.setattr(nb, "_HOP_SCAN_LIMIT", BUDGET)
    monkeypatch.setattr(ops, "_DEGREE_PAGE", 10)
    monkeypatch.setattr(ops, "_DEGREE_ROW_LIMIT", 50)


def world(visible: int, hidden: int, *, hidden_first: bool = True, deny: bool = True) -> FakeGraphProvider:
    """`caller` calls `visible` readable and `hidden` unreadable blocks; every hidden one calls `secret`."""
    g = FakeGraphProvider(deny_records=["rec-h"] if deny else ())
    g.records["rec-h"] = {"_key": "rec-h", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-h"}
    g.code_files["rec-h"] = "src/hidden.py"
    g.edges = []
    for kind, n, rec in (("h", hidden, "rec-h"), ("v", visible, "rec-b")):
        for i in range(n):
            key = f"{kind}{i:03d}"
            g.blocks[key] = _block(key, rec, "x", f"sym_{key}", f"{kind}{i:03d}")
            g.blocks[key].pop("filePath", None)
    keys = [f"h{i:03d}" for i in range(hidden)], [f"v{i:03d}" for i in range(visible)]
    for key in (keys[0] + keys[1] if hidden_first else keys[1] + keys[0]):
        g.edges.append({"_from": f"{BLOCKS}/k_caller", "_to": f"{BLOCKS}/{key}", "relationshipType": "CALLS"})
    for key in keys[0]:
        g.edges.append({"_from": f"{BLOCKS}/{key}", "_to": f"{BLOCKS}/k_target", "relationshipType": "CALLS"})
    return g


async def walk(g, **kw) -> dict:
    return await get_neighbour_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER,
                                    file_path="src/a.py", qualified_name="function:caller",
                                    direction="outbound", **kw)


def names(out) -> list[str]:
    return [n["qualified_name"] for n in out["neighbors"]]


@pytest.mark.parametrize("visible", [0, 3, LIMIT, LIMIT + 2])
async def test_hidden_neighbours_leave_the_walk_byte_identical(visible) -> None:
    bare = await walk(world(visible, 0))
    for hidden in (3, LIMIT + 1, 3 * LIMIT):          # the last exceeds the row limit on its own
        assert await walk(world(visible, hidden)) == bare


@pytest.mark.parametrize("hidden_first", [True, False])
async def test_a_hub_with_more_hidden_rows_than_the_limit_still_returns_every_readable_neighbour(hidden_first) -> None:
    out = await walk(world(LIMIT, 3 * LIMIT, hidden_first=hidden_first))
    assert len(out["neighbors"]) == LIMIT and not out.get("fanout_capped") and not out["truncated"]
    out = await walk(world(LIMIT + 2, 3 * LIMIT, hidden_first=hidden_first))
    assert out["fanout_capped"] is True and out["total"] == LIMIT


async def test_a_hidden_row_never_appears_and_the_walk_never_goes_through_it() -> None:
    out = await walk(world(2, 6), depth=2)
    assert not any(n.startswith("function:h") for n in names(out))
    assert "function:target" not in names(out)          # only the hidden blocks call it
    assert {n["hop"] for n in out["neighbors"]} == {1}


async def test_past_the_scan_budget_incomplete_is_set_alike_whatever_the_extra_rows_are() -> None:
    outs = [await walk(world(1, hidden)) for hidden in (BUDGET + 5, BUDGET + 25)]
    assert outs[0] == outs[1] and outs[0]["incomplete"] is True and outs[0]["truncated"] is True
    assert "incomplete" not in await walk(world(1, BUDGET - 10))


async def test_access_granted_later_shows_on_the_next_call() -> None:
    g = world(1, 3)
    assert len(names(await walk(g))) == 1
    g.deny_records.clear()
    assert len(names(await walk(g))) == 4


async def degrees(g) -> tuple[dict, bool]:
    blocks = [g.blocks["k_caller"], g.blocks["k_panel"]]
    return await _degrees(g, blocks, ["CALLS"], ORG, RecordAccess(g, ORG, USER))


async def test_degree_counts_and_flag_ignore_hidden_rows_below_the_budget() -> None:
    bare = await degrees(world(4, 0))
    for hidden in (5, 12, 30):                          # past one page, still under the budget
        assert await degrees(world(4, hidden)) == bare
    assert bare == ({"k_caller": 4}, False)


async def test_degree_counts_every_readable_edge_when_hidden_ones_come_first() -> None:
    assert (await degrees(world(15, 30)))[0] == {"k_caller": 15}


async def test_degree_is_capped_only_when_the_budget_runs_out() -> None:
    assert (await degrees(world(1, 80)))[1] is True and (await degrees(world(1, 120)))[1] is True


async def test_degree_sees_a_newly_readable_neighbour() -> None:
    g = world(2, 6)
    assert (await degrees(g))[0] == {"k_caller": 2}
    g.deny_records.clear()
    assert (await degrees(g))[0]["k_caller"] == 8


async def test_a_provider_pages_in_cursor_order() -> None:
    g = world(7, 0)
    seen, after = [], ""
    while page := await g.get_neighbors_for_nodes_by_relationship_types(
            ["k_caller"], BLOCKS, ["CALLS"], "outbound", limit=3, after=after):
        seen += [r["key"] for r in page]
        after = page[-1]["cursor"]
    assert seen == sorted(seen) and len(seen) == 7


@pytest.mark.parametrize("edge_types", [None, ["CALLS", "CONTAINS"]])
async def test_a_hub_with_exactly_the_limit_of_callers_and_its_files_contains_edge_is_not_capped(edge_types) -> None:
    # The file's CONTAINS edge comes from a record row, which the walk never shows, so it must not count toward the limit.
    g = world(LIMIT, 0)
    for key in [f"v{i:03d}" for i in range(LIMIT)]:
        g.edges.append({"_from": f"{BLOCKS}/{key}", "_to": f"{BLOCKS}/k_caller", "relationshipType": "CALLS"})
    g.edges.append({"_from": f"{RECORDS}/rec-b", "_to": f"{BLOCKS}/k_caller", "relationshipType": "CONTAINS"})
    out = await get_neighbour_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, file_path="src/a.py",
                                   qualified_name="function:caller", direction="inbound", edge_types=edge_types)
    assert len(out["neighbors"]) == LIMIT and out["total"] == LIMIT
    assert not out.get("fanout_capped") and not out["truncated"]
