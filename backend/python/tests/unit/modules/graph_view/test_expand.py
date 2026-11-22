"""Expand and revalidate end to end over the fake store: oracle, noninterference, revocation, paging, shaping."""
import copy
import itertools

import pytest

from app.agents.actions.code_graph import neighbours as nb
from app.modules.graph_view.models import Candidates, ExpandQuery, GraphEdge
from app.modules.graph_view.service import (
    MAX_OFFSET,
    _shape,
    decode_cursor,
    encode_cursor,
)
from tests.unit.agents.actions.code_graph.conftest import CONN

from .world import (
    Spec,
    World,
    body_bytes,
    expand,
    expected,
    personas,
    revalidate,
    shown,
)

CODE = {"CALLS", "IMPORTS_FROM", "INHERITS", "CONTAINS", "METHOD", "DEFINES", "IMPORTS", "RE_EXPORTS", "EXPORTS",
        "IMPLEMENTS", "OVERRIDES"}


# -- the oracle: the response equals the graph filtered by a rule written apart from the code ---------------

@pytest.mark.parametrize("user", ["alice", "bob", "carol"])
@pytest.mark.parametrize("direction", ["any", "inbound", "outbound"])
@pytest.mark.parametrize("tests", [False, True])
@pytest.mark.parametrize("types", [None, ["CALLS"], ["IMPORTS_FROM", "INHERITS"]])
@pytest.mark.parametrize("anchors", [["main"], ["lib"], ["main", "lib"], ["secret", "tst"], ["other", "main"]])
def test_expand_equals_the_graph_filtered_by_the_rule(user, direction, tests, types, anchors) -> None:
    spec = personas()
    want_nodes, want_edges = expected(spec, user, anchors, direction, set(types) if types else None, tests)
    res = expand(World(spec), user, anchors, direction=direction, includeTests=tests, edgeTypes=types)
    if not want_nodes:
        assert res.status_code == 404
        return
    assert res.status_code == 200, res.text
    got_nodes, got_edges = shown(res)
    assert got_nodes == want_nodes, "a node was shown that should not be, or one was missing"
    assert got_edges == want_edges, "an edge was shown that should not be, or one was missing"


@pytest.mark.parametrize("user", ["alice", "bob", "carol"])
@pytest.mark.parametrize("tests", [False, True])
def test_revalidate_equals_the_rule(user, tests) -> None:
    spec = personas()
    ids = [f"block:{b}" for b in spec.blocks] + [f"record:{r}" for r in spec.records] + ["block:nope", "junk"]
    res = revalidate(World(spec), user, ids, includeTests=tests)
    blocks = {b for b in spec.blocks if expected(spec, user, [b], include_tests=tests)[0]}
    assert res.status_code == 200
    # Records are asked about too: none of them is a node, readable or not.
    assert res.json()["visible"] == [i for i in ids if i in {f"block:{b}" for b in blocks}]


# -- noninterference: hidden data changes nothing a caller can see ----------------------------------

def _with_hidden(spec: Spec, n: int, *, into: str = "main") -> Spec:
    """``spec`` plus ``n`` more hidden blocks, each linked to and from ``into`` and to each other."""
    out = copy.deepcopy(spec)
    for i in range(n):
        rid, key = f"r_hidden{i}", f"hid{i}"
        out.records[rid], out.blocks[key] = CONN, rid
        out.edges += [(into, key, "CALLS"), (key, into, "CALLS"), (key, f"hid{i - 1}" if i else "secret", "CALLS")]
        out.acl["alice"].add(rid)             # alice reads them; bob and carol do not
    return out


@pytest.mark.parametrize("hidden", [1, 3, 40])
@pytest.mark.parametrize("limit", [1, 2, 50])
def test_two_graphs_that_differ_only_in_hidden_nodes_answer_identically(hidden, limit) -> None:
    base, more = personas(), _with_hidden(personas(), hidden)
    for ids in (["main"], ["main", "lib"], ["main", "secret"], ["secret"], ["hid0"], ["nope"]):
        a, b = expand(World(base), "bob", ids, limit=limit), expand(World(more), "bob", ids, limit=limit)
        assert body_bytes(a) == body_bytes(b), (ids, a.text, b.text)
        assert a.headers["cache-control"] == b.headers["cache-control"]


def test_hidden_edges_do_not_move_the_page_boundary() -> None:
    base, more = personas(), _with_hidden(personas(), 9)
    for spec in (base, more):
        spec.edges = [e for e in spec.edges if e[0] != "tst"]
    pages = []
    for spec in (base, more):
        cursor, seen = None, []
        for _ in range(5):
            res = expand(World(spec), "bob", ["main"], limit=1, **({"cursor": cursor} if cursor else {}))
            seen.append(body_bytes(res))
            cursor = res.json()["page"].get("nextCursor")
            if not cursor:
                break
        pages.append(seen)
    assert pages[0] == pages[1] and len(pages[0]) >= 2


def test_past_the_scan_budget_the_response_says_incomplete_and_never_adds_a_node(monkeypatch) -> None:
    monkeypatch.setattr(nb, "_HOP_SCAN_LIMIT", 40)
    small, big, huge = (_with_hidden(personas(), n) for n in (2, 20, 60))
    # Pages of 3 raw rows: 2 hidden blocks (about 10 rows) fit the 40-row budget, 20 and 60 do not.
    under, over_a, over_b = (expand(World(spec), "bob", ["main"], limit=2) for spec in (small, big, huge))
    assert under.json()["page"]["incomplete"] is False and len(under.json()["edges"]) == 2
    # Beyond the budget the scan stops early: the bit says so, and what was reached is a subset of the whole.
    whole = shown(expand(World(big), "bob", ["main"]))
    for over in (over_a, over_b):
        assert over.json()["page"]["incomplete"] is True
        assert shown(over)[0] <= whole[0] and shown(over)[1] <= whole[1]


# -- revocation and grants take effect on the next request --------------------------------------------

def test_a_revoked_file_is_gone_on_the_next_request_and_back_when_granted() -> None:
    world = World(personas())
    assert "secret" in shown(expand(world, "alice", ["main"]))[0]
    world.spec.acl["alice"].discard("r_secret")
    assert "secret" not in shown(expand(world, "alice", ["main"]))[0]
    assert revalidate(world, "alice", ["block:secret"]).json()["visible"] == []
    world.spec.acl["alice"].add("r_secret")
    assert "secret" in shown(expand(world, "alice", ["main"]))[0]
    assert revalidate(world, "alice", ["block:secret"]).json()["visible"] == ["block:secret"]


def test_a_trashed_and_restored_record_follows_the_same_way() -> None:
    world = World(personas())
    trashed = world.records.pop("r_lib")
    assert "lib" not in shown(expand(world, "bob", ["main"]))[0]
    assert revalidate(world, "bob", ["block:lib", "record:r_lib"]).json()["visible"] == []
    world.records["r_lib"] = trashed
    assert "lib" in shown(expand(world, "bob", ["main"]))[0]


def test_a_revoked_anchor_is_a_404_on_the_next_request() -> None:
    world = World(personas())
    assert expand(world, "bob", ["lib"]).status_code == 200
    world.spec.acl["bob"].discard("r_lib")
    assert expand(world, "bob", ["lib"]).status_code == 404


# -- hidden and missing anchors are one answer ---------------------------------------------------------

def test_a_hidden_anchor_in_a_batch_is_dropped_like_a_missing_one() -> None:
    world = World(personas())
    hidden, missing = expand(world, "bob", ["main", "secret"]), expand(world, "bob", ["main", "nope"])
    assert body_bytes(hidden) == body_bytes(missing) and hidden.status_code == 200
    assert [a["id"] for a in hidden.json()["anchors"]] == ["block:main"]


def test_all_anchors_hidden_or_missing_is_the_same_404() -> None:
    world = World(personas())
    answers = {body_bytes(expand(world, "bob", ids)) for ids in (["secret"], ["nope"], ["secret", "nope"], ["tst"])}
    other_org = personas()
    other_org.orgs["lib"] = "org-elsewhere"
    answers.add(body_bytes(expand(World(other_org), "bob", ["lib"])))
    assert len(answers) == 1
    assert expand(world, "bob", ["nope"]).status_code == 404


# -- shape, paging, totals -----------------------------------------------------------------------------

def test_the_response_shape() -> None:
    body = expand(World(personas()), "bob", ["main"], edgeTypes=["CALLS"], direction="outbound").json()
    assert set(body) == {"nodes", "edges", "anchors", "page", "graphStatus"}
    assert body["nodes"][0] == {"id": "block:main", "kind": "function", "label": "main", "recordId": "r_main",
                                "file": "src/r_main.py", "line": 1}
    (edge,) = body["edges"]
    assert edge["from"] == "block:main" and edge["to"] == "block:lib" and edge["type"] == "CALLS"
    assert edge["id"].startswith("e:") and edge["line"] == 7 and edge["confidence"] == "EXTRACTED"
    assert body["anchors"] == [{"id": "block:main", "total": 1}]
    assert body["page"] == {"truncated": False, "incomplete": False} and body["graphStatus"] == "ready"


def test_pages_cover_every_edge_once_and_total_is_exact_when_not_truncated() -> None:
    spec = personas()
    spec.acl["bob"].add("r_secret")
    every = expand(World(spec), "bob", ["main"], limit=100).json()
    assert every["page"]["truncated"] is False and every["anchors"][0]["total"] == len(every["edges"])
    seen, cursor = [], None
    while True:
        page = expand(World(spec), "bob", ["main"], limit=2, **({"cursor": cursor} if cursor else {})).json()
        seen += [e["id"] for e in page["edges"]]
        assert len(page["edges"]) <= 2
        cursor = page["page"].get("nextCursor")
        if not cursor:
            break
        assert page["page"]["truncated"] is True
    assert seen == [e["id"] for e in every["edges"]] and len(set(seen)) == len(seen)


def test_a_truncated_total_is_a_lower_bound() -> None:
    res = expand(World(personas()), "alice", ["main"], limit=1).json()
    assert res["page"]["truncated"] is True and res["anchors"][0]["total"] >= 1
    assert len(res["edges"]) == 1 and res["page"]["nextCursor"]


def test_a_read_stops_one_readable_row_past_the_limit(monkeypatch) -> None:
    sizes: list[int] = []
    real = nb._readable_neighbour_rows

    async def spy(*args, **kw) -> object:
        sizes.append(kw["want"])
        return await real(*args, **kw)

    monkeypatch.setattr(nb, "_readable_neighbour_rows", spy)
    expand(World(personas()), "alice", ["main"], limit=3)
    assert sizes == [4]                              # the page and one more, to see whether another edge follows
    expand(World(personas()), "alice", ["main"], limit=3, cursor=encode_cursor(6))
    assert sizes == [4, 10]


def test_a_duplicate_anchor_id_is_one_anchor() -> None:
    assert expand(World(personas()), "bob", ["main", "main"]).json()["anchors"] == [{"id": "block:main", "total": 2}]


def test_the_same_edge_seen_from_both_anchors_is_one_edge() -> None:
    body = expand(World(personas()), "bob", ["main", "lib"], edgeTypes=["CALLS"]).json()
    assert [(e["from"], e["to"]) for e in body["edges"]] == [("block:main", "block:lib")]
    assert body["anchors"] == [{"id": "block:main", "total": 1}, {"id": "block:lib", "total": 1}]


# -- cursors -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cursor", [
    "!!!", "", "x" * 33, "x" * 256, "djI6MQ", encode_cursor(0).replace("A", "-")[:-1] + "*",
    "djE6LTE", "djE6YWJj", "djE6MQ==", "djE6MTAwMDAw", "djE6IDE", "djE6MV4", "é",
])
def test_a_malformed_cursor_is_a_400_that_reveals_nothing(cursor) -> None:
    good = expand(World(personas()), "bob", ["nope"], cursor=cursor)       # hidden anchor: the cursor is judged first
    other = expand(World(personas()), "bob", ["main"], cursor=cursor)
    assert good.status_code == other.status_code == 400
    assert good.json() == other.json() == {"detail": "Invalid cursor."}


def test_cursors_round_trip_and_are_bounded() -> None:
    assert decode_cursor(None) == 0 and decode_cursor(encode_cursor(0)) == 0
    assert decode_cursor(encode_cursor(MAX_OFFSET)) == MAX_OFFSET
    with pytest.raises(Exception, match="Invalid cursor"):
        decode_cursor(encode_cursor(MAX_OFFSET + 1))


def test_unknown_edge_types_are_a_400() -> None:
    res = expand(World(personas()), "bob", ["main"], edgeTypes=["CALLS", "PERMISSION"])
    assert res.status_code == 400 and "Valid:" in res.json()["detail"]


# -- the oracle over many shapes: nothing missing, nothing extra --------------------------------------

def test_every_choice_of_readable_files_matches_the_rule() -> None:
    spec = personas()
    files = sorted(spec.records)
    for mask in itertools.product([False, True], repeat=len(files)):
        spec.acl["u"] = {f for f, on in zip(files, mask, strict=True) if on}
        for anchors in (["main"], ["lib", "secret"]):
            want_nodes, want_edges = expected(spec, "u", anchors)
            res = expand(World(spec), "u", anchors)
            if not want_nodes:
                assert res.status_code == 404
            else:
                assert shown(res) == (want_nodes, want_edges), (mask, anchors)


def test_anchors_and_nodes_follow_the_request_order_not_the_stores() -> None:
    world = World(personas())
    real = world.get_nodes_by_field_in

    async def backwards(*a, **k) -> list:
        return list(reversed(await real(*a, **k)))

    world.get_nodes_by_field_in = backwards
    for ids in (["main", "lib"], ["lib", "main"]):
        body = expand(world, "bob", ids).json()
        assert [a["id"] for a in body["anchors"]] == [f"block:{i}" for i in ids]
        assert [n["id"] for n in body["nodes"][:2]] == [f"block:{i}" for i in ids]


def test_parallel_edges_do_not_cost_a_page_its_rows_or_hide_what_follows() -> None:
    spec = personas()
    spec.edges = [("main", "lib", "CALLS")] * 6 + [("main", "secret", "CALLS"), ("lib", "main", "IMPORTS_FROM"),
                                                    ("main", "tst", "CALLS")]
    spec.acl["bob"].add("r_test")
    whole = {e["id"] for e in expand(World(spec), "bob", ["main"], limit=100, includeTests=True).json()["edges"]}
    assert len(whole) == 3
    seen, cursor = [], None
    for _ in range(6):
        page = expand(World(spec), "bob", ["main"], limit=2, includeTests=True, **({"cursor": cursor} if cursor else {})).json()
        seen += [e["id"] for e in page["edges"]]
        cursor = page["page"].get("nextCursor")
        if not cursor:
            break
    assert sorted(seen) == sorted(whole) and len(seen) == len(set(seen))


def test_an_edge_between_two_anchors_does_not_shorten_a_page() -> None:
    spec = personas()
    page = expand(World(spec), "alice", ["main", "lib"], limit=2, edgeTypes=["CALLS", "IMPORTS_FROM"]).json()
    assert len(page["edges"]) == 2 and page["page"]["truncated"] is True


def test_the_last_cursor_is_one_the_server_accepts() -> None:
    edge = GraphEdge.model_validate({"id": "e", "from": "block:a", "to": "block:b", "type": "CALLS"})
    found = Candidates(anchors=["block:a"], edges=[edge] * (MAX_OFFSET + 3), truncated=True)

    def next_cursor(offset: int) -> str | None:
        return _shape(found, ExpandQuery(["block:a"], "any", None, False, 2, offset)).page.nextCursor

    assert decode_cursor(next_cursor(MAX_OFFSET - 2)) == MAX_OFFSET
    assert next_cursor(MAX_OFFSET - 1) is None
    assert _shape(found, ExpandQuery(["block:a"], "any", None, False, 2, MAX_OFFSET - 1)).page.truncated is True


def test_an_empty_page_never_says_there_is_more() -> None:
    # A read that stopped at its row limit with every row cut by a connected app's limit comes back empty.
    found = Candidates(anchors=["block:a"], edges=[], truncated=True)
    page = _shape(found, ExpandQuery(["block:a"], "any", None, False, 20, 0)).page
    assert page.truncated is False and page.nextCursor is None
