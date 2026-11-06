"""Rows a caller cannot read (or did not ask for) must not move the other graph tools' answers, nor push readable rows out."""
import pytest

from app.agents.actions.code_graph import neighbours as nb
from app.agents.actions.code_graph import ops, query
from app.agents.actions.code_graph import path as path_mod
from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.ops import (
    RecordAccess,
    _degrees,
    _readable_neighbour_rows,
)
from app.agents.actions.code_graph.path import find_symbol_path_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import BLOCKS, CONN, ORG, USER, FakeGraphProvider, _block

pytestmark = pytest.mark.asyncio

LIMIT = 5
HUB = 4 * LIMIT        # more hidden rows than any page or limit below


@pytest.fixture(autouse=True)
def small_caps(monkeypatch) -> None:
    monkeypatch.setattr(nb, "_ROWS_PER_ANCHOR", LIMIT)
    monkeypatch.setattr(query, "_ENTRY_CALLER_PAGE", LIMIT)
    monkeypatch.setattr(path_mod, "_FRONTIER_LIMIT", LIMIT)
    monkeypatch.setattr(path_mod, "_CALLER_PROBE", LIMIT)
    monkeypatch.setattr(ops, "_DEGREE_PAGE", 1)


def hub(n: int, *, rec: str = "rec-h", connector: str = CONN, prefix: str = "h", role: str | None = None,
        deny: bool = True) -> FakeGraphProvider:
    """A graph with no edges plus `n` extra blocks in `rec`, hidden from the caller unless `deny` is off."""
    g = FakeGraphProvider(deny_records=[rec] if deny else (), file_roles={rec: role} if role else None)
    g.records[rec] = {"_key": rec, "orgId": ORG, "connectorId": connector, "virtualRecordId": f"v-{rec}"}
    g.code_files[rec] = f"src/{rec}.py"
    g.edges = []
    for i in range(n):
        key = f"{prefix}{i:03d}"
        g.blocks[key] = {**_block(key, rec, "x", f"sym_{key}", key), "connectorId": connector}
        g.blocks[key].pop("filePath", None)
    return g


def call(g, src: str, dst: str, rel: str = "CALLS") -> None:
    g.edges.append({"_from": f"{BLOCKS}/{src}", "_to": f"{BLOCKS}/{dst}", "relationshipType": rel})


# -- entry points ------------------------------------------------------------


async def entries(g) -> dict:
    return await query.entry_points_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, limit=100)


async def test_entry_points_do_not_count_callers_the_user_cannot_read() -> None:
    bare = await entries(hub(0))
    for n in (3, HUB):
        g = hub(n)
        for i in range(n):
            call(g, f"h{i:03d}", "k_target")
        assert await entries(g) == bare
    assert "function:target" in {e["qualified_name"] for e in bare["entry_points"]}


async def test_a_readable_caller_still_counts_behind_a_hub_of_hidden_ones() -> None:
    g = hub(HUB)
    for i in range(HUB):
        call(g, f"h{i:03d}", "k_target")
    call(g, "k_caller", "k_target")
    out = await entries(g)
    assert "function:target" not in {e["qualified_name"] for e in out["entry_points"]}
    assert "incomplete" not in out


# -- path --------------------------------------------------------------------


async def path(g, b=("src/b.py", "function:target"), **kw) -> dict:
    return without_marker(await find_symbol_path_impl(
        graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path_a="src/a.py", qualified_name_a="function:caller",
        file_path_b=b[0], qualified_name_b=b[1], **kw))


def with_caller_edges(n: int) -> FakeGraphProvider:
    g = hub(n)
    call(g, "k_caller", "k_target")
    for i in range(n):
        call(g, "k_caller", f"h{i:03d}")
    return g


async def test_hidden_neighbours_neither_cap_the_path_search_nor_crowd_out_the_path() -> None:
    bare = await path(with_caller_edges(0))
    assert bare["found"] is True and "capped" not in bare
    for n in (3, HUB):
        assert await path(with_caller_edges(n)) == bare


async def test_a_hub_of_hidden_neighbours_does_not_hide_the_constructor_hop() -> None:
    def build(n: int) -> FakeGraphProvider:
        g = hub(n)
        g.blocks["k_init"] = {**_block("k_init", "rec-b", "x", "src_b_init", "__init__", "method"),
                              "parentBlockId": "k_class"}
        g.blocks["k_init"].pop("filePath")
        call(g, "k_caller", "k_class")
        call(g, "k_class", "k_init", "METHOD")
        for i in range(n):
            call(g, "k_class", f"h{i:03d}", "CONTAINS")
        return g
    bare = await path(build(0), b=("src/b.py", "method:__init__"))
    assert bare["found"] is True and bare["hops"][-1]["to"]["qualified_name"] == "method:__init__"
    assert await path(build(HUB), b=("src/b.py", "method:__init__")) == bare


async def test_the_unproven_note_sees_a_readable_caller_behind_a_hub_of_hidden_ones() -> None:
    def build(n: int, readable_caller: bool) -> FakeGraphProvider:
        g = hub(n)
        for i in range(n):
            call(g, f"h{i:03d}", "k_panel")
        if readable_caller:
            call(g, "k_client", "k_panel")
        return g
    panel = ("web/ui/panel.ts", "function:renderPanel")
    assert (await path(build(HUB, True), b=panel))["note"] == (await path(build(0, True), b=panel))["note"]
    assert (await path(build(HUB, True), b=panel))["note"].startswith("No chain of linked calls")
    assert (await path(build(HUB, False), b=panel)) == (await path(build(0, False), b=panel))


# -- test-role and connector rows, filtered before they count ------------------


async def walk(g, **kw) -> dict:
    return await get_neighbour_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER,
                                    file_path="src/a.py", qualified_name="function:caller",
                                    direction="outbound", **kw)


def callees(g, prefix: str, n: int) -> None:
    for i in range(n):
        call(g, "k_caller", f"{prefix}{i:03d}")


async def test_a_hub_of_test_rows_does_not_push_out_the_rows_asked_for() -> None:
    g = hub(HUB, rec="rec-t", prefix="a", role="test", deny=False)   # sorts first
    for i in range(LIMIT):
        g.blocks[f"z{i}"] = {**_block(f"z{i}", "rec-b", "x", f"sym_z{i}", f"z{i}"), "connectorId": CONN}
        g.blocks[f"z{i}"].pop("filePath")
    callees(g, "a", HUB)
    for i in range(LIMIT):
        call(g, "k_caller", f"z{i}")
    out = await walk(g)
    assert len(out["neighbors"]) == LIMIT and not out["truncated"] and out["hidden_tests"] == HUB
    assert len((await walk(g, include_tests=True))["neighbors"]) == LIMIT      # capped, not missing


async def test_a_hub_of_other_connector_rows_does_not_push_out_the_rows_asked_for() -> None:
    g = hub(HUB, rec="rec-x", prefix="a", connector="conn-other", deny=False)
    for i in range(LIMIT):
        g.blocks[f"z{i}"] = {**_block(f"z{i}", "rec-b", "x", f"sym_z{i}", f"z{i}"), "connectorId": CONN}
        g.blocks[f"z{i}"].pop("filePath")
    callees(g, "a", HUB)
    for i in range(LIMIT):
        call(g, "k_caller", f"z{i}")
    out = await walk(g)
    assert len(out["neighbors"]) == LIMIT and not out["truncated"] and not out.get("fanout_capped")


# -- cursor ties and a provider that does not page ------------------------------


async def test_parallel_edges_split_by_a_page_boundary_are_all_counted() -> None:
    g = hub(0)
    for _ in range(3):
        call(g, "k_caller", "k_target")
    blocks = [g.blocks["k_caller"], g.blocks["k_panel"]]
    paged = await _degrees(g, blocks, ["CALLS"], ORG, RecordAccess(g, ORG, USER))      # one row per page
    assert paged == ({"k_caller": 3}, False)


class Unpaged(FakeGraphProvider):
    def __init__(self, cursor) -> None:
        super().__init__()
        self.cursor = cursor

    async def get_neighbors_for_nodes_by_relationship_types(self, *a, limit=5000, **kw) -> list[dict]:
        return [{"anchorKey": "k_caller", "collection": BLOCKS, "key": "k_target", "direction": "outbound",
                 "relationshipType": "CALLS", **self.cursor} for _ in range(limit)]


@pytest.mark.parametrize("cursor", [{}, {"cursor": "same"}])
async def test_a_provider_that_ignores_after_is_an_error_not_a_loop_or_a_keyerror(cursor) -> None:
    g = Unpaged(cursor)
    with pytest.raises(RuntimeError, match="Failed to walk"):
        await _readable_neighbour_rows(g, ORG, RecordAccess(g, ORG, USER), ["k_caller"], ["CALLS"], "outbound",
                                       page=2, budget=100)


async def test_hidden_tests_do_not_depend_on_where_a_hidden_row_puts_the_page_break() -> None:
    """Page size is LIMIT + 1 = 6; one unreadable row sorted first moves the break past a test callee."""
    def build(hidden: bool) -> FakeGraphProvider:
        g = hub(1, rec="rec-t", prefix="v005a", role="test", deny=False)     # sorts between v005 and v006
        for i in range(8):
            g.blocks[f"v{i:03d}"] = {**_block(f"v{i:03d}", "rec-b", "x", f"sym_v{i}", f"v{i:03d}"), "connectorId": CONN}
            g.blocks[f"v{i:03d}"].pop("filePath")
            call(g, "k_caller", f"v{i:03d}")
        call(g, "k_caller", "v005a000")
        if hidden:
            g.records["rec-h"] = {"_key": "rec-h", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-h"}
            g.deny_records.add("rec-h")
            g.blocks["a000"] = {**_block("a000", "rec-h", "x", "sym_a000", "a000"), "connectorId": CONN}
            g.blocks["a000"].pop("filePath")
            call(g, "k_caller", "a000")
        return g
    bare, with_hidden = await walk(build(False)), await walk(build(True))
    assert with_hidden == bare and bare["fanout_capped"] is True and "hidden_tests" not in bare


async def test_degrees_with_the_real_row_limit_ignore_many_hidden_rows(monkeypatch) -> None:
    monkeypatch.setattr(ops, "_DEGREE_ROW_LIMIT", 50000)
    g = hub(60)
    for i in range(60):
        call(g, "k_caller", f"h{i:03d}")
    for _ in range(4):
        call(g, "k_caller", "k_target")
    out = await _degrees(g, [g.blocks["k_caller"], g.blocks["k_panel"]], ["CALLS"], ORG, RecordAccess(g, ORG, USER))
    assert out == ({"k_caller": 4}, False)


async def test_a_cursor_that_advances_in_the_stores_collation_only_is_not_an_error() -> None:
    class Collated(FakeGraphProvider):
        pages = iter([[{"collection": BLOCKS, "key": "k_target", "cursor": "b"}] * 2,
                      [{"collection": BLOCKS, "key": "k_target", "cursor": "a"}] * 2, []])

        async def get_neighbors_for_nodes_by_relationship_types(self, **kw) -> list[dict]:
            return next(self.pages)

    g = Collated()
    rows, _, _ = await _readable_neighbour_rows(g, ORG, RecordAccess(g, ORG, USER), ["k_caller"], ["CALLS"],
                                                "outbound", page=2, budget=100)
    assert len(rows) == 4


async def test_degrees_log_a_failed_fetch_but_raise_a_failed_access_check() -> None:
    blocks = lambda g: [g.blocks["k_caller"], g.blocks["k_panel"]]  # noqa: E731

    class FetchFails(FakeGraphProvider):
        async def get_neighbors_for_nodes_by_relationship_types(self, **kw) -> list[dict]:
            raise RuntimeError("store down")

    class AccessFails(FakeGraphProvider):
        async def get_nodes_by_field_in(self, collection, *a, **kw) -> list[dict]:
            if collection == BLOCKS:
                raise RuntimeError("access check down")
            return await super().get_nodes_by_field_in(collection, *a, **kw)

    g = FetchFails()
    assert await _degrees(g, blocks(g), ["CALLS"], ORG, RecordAccess(g, ORG, USER)) == ({}, False)
    g = AccessFails()
    with pytest.raises(RuntimeError, match="access check down"):
        await _degrees(g, blocks(g), ["CALLS"], ORG, RecordAccess(g, ORG, USER))


class StoreOrdered(FakeGraphProvider):
    """Returns a fixed stream in pages, with cursors that sort the opposite way to the stream (as a collation can)."""
    stream = ("k_caller", "k_target", "k_panel", "k_client", "k_class")      # k_target and k_class are test files

    def __init__(self) -> None:
        super().__init__(file_roles={"rec-b": "test"})
        self.rows = [{"anchorKey": "k_caller", "collection": BLOCKS, "key": k, "direction": "outbound",
                      "relationshipType": "CALLS", "cursor": chr(ord("z") - i)} for i, k in enumerate(self.stream)]

    async def get_neighbors_for_nodes_by_relationship_types(self, *, limit, after, **kw) -> list[dict]:
        start = 0 if after == "" else next(i for i, r in enumerate(self.rows) if r["cursor"] == after) + 1
        return self.rows[start:start + limit]


async def stream_rows(want: int) -> tuple[list[dict], bool]:
    g = StoreOrdered()
    rows, over, _ = await _readable_neighbour_rows(g, ORG, RecordAccess(g, ORG, USER), ["k_caller"], ["CALLS"],
                                                   "outbound", page=3, budget=100, want=want, include_tests=False)
    return rows, over


async def test_test_rows_are_kept_by_stream_position_not_by_cursor_text() -> None:
    rows, over = await stream_rows(2)
    assert over and [r["key"] for r in rows] == ["k_caller", "k_panel", "k_target"]     # k_class sits after the cut


async def test_a_zero_want_keeps_no_test_rows() -> None:
    assert await stream_rows(0) == ([], True)


async def test_a_hub_of_readable_record_rows_does_not_push_out_the_block_rows_asked_for() -> None:
    g = hub(0)
    for i in range(HUB):
        g.records[f"a{i:03d}"] = {"_key": f"a{i:03d}", "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v{i}"}
        g.edges.append({"_from": f"{BLOCKS}/k_caller", "_to": f"records/a{i:03d}", "relationshipType": "IMPORTS_FROM"})
    for key in ("k_target", "k_method", "k_client", "k_panel", "k_class"):
        call(g, "k_caller", key)
    out = await walk(g)
    assert len(out["neighbors"]) == 5 and not out["truncated"] and not out.get("fanout_capped")
