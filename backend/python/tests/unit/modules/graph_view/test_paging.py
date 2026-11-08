"""Walk whole hubs by cursor and compare with the rule: complete, no repeats, no dead ends.
"""
import random

import pytest

from app.agents.actions.code_graph.ops import CODE_RELATIONS
from tests.unit.agents.actions.code_graph.conftest import CONN
from tests.unit.modules.graph_view.world import (
    OTHER_CONN,
    Spec,
    World,
    expand,
    expected,
)


def pages_of(spec: Spec, user: str, anchors: list[str], limit: int, **body) -> list:
    cursor, pages = None, []
    for _ in range(400):
        res = expand(World(spec), user, anchors, limit=limit, **body, **({"cursor": cursor} if cursor else {}))
        assert res.status_code == 200, res.text
        page = res.json()
        pages.append(page)
        cursor = page["page"].get("nextCursor")
        if not cursor:
            return pages
    raise AssertionError("a walk by cursor did not end")


def random_spec(rng: random.Random) -> tuple[Spec, list[str]]:
    n_rec, n_blk = rng.randint(2, 6), rng.randint(3, 12)
    records = {f"r{i}": rng.choice([CONN, CONN, OTHER_CONN]) for i in range(n_rec)}
    blocks = {}
    for i in range(n_blk):
        rid = rng.choice(sorted(records))
        blocks[f"b{i}"] = rid
    keys = sorted(blocks)
    edges = [(rng.choice(keys), rng.choice(keys), rng.choice(CODE_RELATIONS)) for _ in range(rng.randint(3, 40))]
    edges += [edges[0]] * rng.randint(0, 3)                      # parallel edges
    spec = Spec(records=records, blocks=blocks, edges=edges,
                test_records={r for r in records if rng.random() < 0.25})
    spec.acl = {"u": {r for r in records if rng.random() < 0.75}}
    return spec, rng.sample(keys, rng.randint(1, min(3, len(keys))))


@pytest.mark.parametrize("seed", range(120))
@pytest.mark.parametrize("limit", [1, 2, 3])
def test_walking_a_hub_by_cursor_returns_every_edge_once_and_ends(seed, limit) -> None:
    rng = random.Random(seed)
    spec, anchors = random_spec(rng)
    tests = rng.random() < 0.5
    direction = rng.choice(["any", "inbound", "outbound"])
    want_nodes, want_edges = expected(spec, "u", anchors, direction, None, tests)
    first = expand(World(spec), "u", anchors, limit=limit, direction=direction, includeTests=tests)
    if not want_nodes:
        assert first.status_code == 404
        return
    pages = pages_of(spec, "u", anchors, limit, direction=direction, includeTests=tests)
    ids = [e["id"] for p in pages for e in p["edges"]]
    triples = {(e["from"][6:], e["to"][6:], e["type"]) for p in pages for e in p["edges"]}
    assert len(ids) == len(set(ids)), "an edge came back on two pages"
    assert triples == want_edges, "pages are not the whole hub"
    assert all(len(p["edges"]) <= limit for p in pages)
    for p in pages[:-1]:
        assert p["page"]["truncated"] is True and p["page"]["nextCursor"]
    assert pages[-1]["page"]["truncated"] is False or not pages[-1]["page"].get("nextCursor")
    # A page that says "more" and holds nothing is a dead end.
    for p in pages:
        assert not (p["page"]["truncated"] and not p["edges"]), "empty page with truncated=true"
