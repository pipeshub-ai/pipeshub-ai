"""Random graphs and random access: taking access away never adds a node, an edge or a visible id."""
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agents.actions.code_graph.ops import CODE_RELATIONS
from tests.unit.agents.actions.code_graph.conftest import CONN, ORG

from .world import Spec, World, client, expected

RECORDS = [f"r{i}" for i in range(5)]
BLOCKS = {f"b{i}": RECORDS[i % len(RECORDS)] for i in range(10)}


@st.composite
def worlds(draw) -> tuple[Spec, set[str], set[str]]:
    pairs = st.tuples(st.sampled_from(sorted(BLOCKS)), st.sampled_from(sorted(BLOCKS)), st.sampled_from(CODE_RELATIONS))
    spec = Spec(
        records=dict.fromkeys(RECORDS, CONN), blocks=dict(BLOCKS),
        edges=draw(st.lists(pairs.filter(lambda e: e[0] != e[1]), max_size=30, unique=True)),
        test_records=set(draw(st.sets(st.sampled_from(RECORDS), max_size=2))),
    )
    before = set(draw(st.sets(st.sampled_from(RECORDS))))
    after = set(draw(st.sets(st.sampled_from(sorted(before)) if before else st.nothing())))
    return spec, before, after


def seen(spec: Spec, readable: set[str], anchor: str, direction: str, tests: bool):  # noqa: ANN201
    spec.acl = {"u": readable}
    res = client(World(spec), {"orgId": ORG, "userId": "u"}).post("/api/v1/graph/expand", json={
        "source": "code", "nodeIds": [f"block:{anchor}"], "direction": direction, "includeTests": tests, "limit": 100})
    if res.status_code == 404:
        return set(), set(), res.status_code, set(), set()
    body = res.json()
    return ({n["id"] for n in body["nodes"]}, {e["id"] for e in body["edges"]}, res.status_code,
            {(e["from"].removeprefix("block:"), e["to"].removeprefix("block:"), e["type"]) for e in body["edges"]},
            {n["id"].removeprefix("block:") for n in body["nodes"]})


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(worlds(), st.sampled_from(sorted(BLOCKS)), st.sampled_from(["any", "inbound", "outbound"]), st.booleans())
def test_revoking_never_adds_and_every_answer_matches_the_rule(case, anchor, direction, tests) -> None:
    spec, before, after = case
    wide, narrow = seen(spec, before, anchor, direction, tests), seen(spec, after, anchor, direction, tests)
    assert narrow[0] <= wide[0] and narrow[1] <= wide[1], "revoking access added a node or an edge"
    for readable, got in ((before, wide), (after, narrow)):
        spec.acl = {"u": readable}
        want_nodes, want_edges = expected(spec, "u", [anchor], direction, None, tests)
        if got[2] == 404:
            assert not want_nodes
        else:
            assert (got[4], got[3]) == (want_nodes, want_edges)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(worlds())
def test_revoking_never_adds_a_visible_id(case) -> None:
    spec, before, after = case
    ids = [f"block:{b}" for b in BLOCKS]

    def visible(readable: set[str]) -> set[str]:
        spec.acl = {"u": readable}
        res = client(World(spec), {"orgId": ORG, "userId": "u"}).post(
            "/api/v1/graph/revalidate", json={"source": "code", "nodeIds": ids})
        return set(res.json()["visible"])

    assert visible(after) <= visible(before)
