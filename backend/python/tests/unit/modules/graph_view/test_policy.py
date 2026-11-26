"""The visibility policy, one row per rule: what is shown, and what is not, for the same graph."""
import pytest

from app.agents.actions.code_graph.ops import RecordAccess
from app.modules.graph_view.models import Candidates, GraphEdge, GraphNode
from app.modules.graph_view.policy import ALLOWED_EDGE_TYPES, VisibilityPolicy
from tests.unit.agents.actions.code_graph.conftest import ORG

from .world import Spec, World, personas


def policy(world: World, user: str = "bob", *, tests: bool = False, outside=None) -> VisibilityPolicy:
    return VisibilityPolicy(world, ORG, RecordAccess(world, ORG, user), include_tests=tests, outside_limit=outside,
                            groups_outside=outside)


@pytest.mark.parametrize(("user", "tests", "node", "shown"), [
    ("alice", False, "block:main", True),
    ("bob", False, "block:lib", True),
    ("bob", False, "block:secret", False),         # file not readable
    ("carol", False, "block:main", False),         # nothing readable
    ("bob", False, "block:tst", False),            # test code hidden unless asked for
    ("bob", True, "block:tst", True),
    ("bob", True, "block:secret", False),          # asking for tests does not widen access
    ("bob", False, "block:nope", False),           # missing is hidden
    ("bob", False, "record:r_lib", False),         # only code symbols have node ids; a record, readable or not, is no node
    ("bob", False, "record:r_secret", False),
    ("bob", False, "main", False),                 # malformed ids are hidden, not errors
    ("bob", False, "block:", False),
    ("bob", False, "edge:1", False),
    ("bob", False, "", False),
])
async def test_visible_table(user: str, tests: bool, node: str, shown: bool) -> None:
    assert (node in await policy(World(personas()), user, tests=tests).visible([node])) is shown


async def test_a_block_of_another_org_is_hidden() -> None:
    spec = personas()
    spec.orgs["lib"] = "org-elsewhere"
    assert await policy(World(spec)).visible(["block:lib", "block:main"]) == {"block:main"}


async def test_a_node_outside_a_connected_apps_limit_is_hidden() -> None:
    asked: list[set[str]] = []

    async def outside(ids: set[str]) -> set[str]:
        asked.append(set(ids))
        return {"repo-1"}                          # every fake block sits in repo-1

    world = World(personas())
    assert await policy(world, outside=outside).visible(["block:main", "block:lib"]) == set()
    assert {"repo-1"} in asked


async def test_a_batch_of_thousands_is_decided_in_pieces() -> None:
    world = World(personas())
    calls: list[int] = []
    real = world.get_nodes_by_field_in

    async def counting(collection, field_name, field_values, *a, **k) -> object:
        calls.append(len(field_values))
        return await real(collection, field_name, field_values, *a, **k)

    world.get_nodes_by_field_in = counting
    ids = [f"block:b{i}" for i in range(1200)] + ["block:main"]
    assert await policy(world).visible(ids) == {"block:main"}
    assert max(calls) <= 1000 and sum(calls) >= 1201


def _cands(*edges: tuple[str, str, str], anchors: tuple[str, ...] = ("block:main",)) -> Candidates:
    ends = {x for e in edges for x in e[:2]} | set(anchors)
    return Candidates(
        nodes=[GraphNode(id=i) for i in sorted(ends)],
        edges=[GraphEdge(id=f"e{n}", from_=a, to=b, type=t) for n, (a, b, t) in enumerate(edges)],
        anchors=list(anchors),
    )


async def test_trim_keeps_an_edge_only_when_both_ends_are_visible() -> None:
    out = await policy(World(personas())).trim(_cands(
        ("block:main", "block:lib", "CALLS"), ("block:main", "block:secret", "CALLS"),
        ("block:tst", "block:main", "CALLS"),
    ))
    assert [(e.from_, e.to) for e in out.edges] == [("block:main", "block:lib")]
    assert {n.id for n in out.nodes} == {"block:main", "block:lib"}   # a hidden end leaves no node behind


@pytest.mark.parametrize("kind", ["PERMISSION", "MEMBER_OF", "BELONGS_TO", "ANYTHING_ELSE", "calls", ""])
async def test_trim_never_shows_an_edge_type_outside_the_allowlist(kind: str) -> None:
    out = await policy(World(personas())).trim(_cands(("block:main", "block:lib", kind)))
    assert out.edges == [] and {n.id for n in out.nodes} == {"block:main"}


def test_the_allowlist_is_the_code_relations_and_no_access_relation() -> None:
    assert {"CALLS", "IMPORTS_FROM", "INHERITS", "CONTAINS"} <= ALLOWED_EDGE_TYPES
    assert not ALLOWED_EDGE_TYPES & {"PERMISSION", "MEMBER_OF", "BELONGS_TO", "OWNER", "USER", "GROUP"}


async def test_trim_walks_only_from_a_visible_anchor() -> None:
    world = World(personas())
    # lib -> main touches no anchor; the walk started at secret, which bob cannot see.
    out = await policy(world).trim(_cands(("block:lib", "block:main", "CALLS"),
                                          ("block:secret", "block:lib", "CALLS"), anchors=("block:secret",)))
    assert out.anchors == [] and out.edges == [] and out.nodes == []


async def test_a_verdict_is_not_kept_past_its_request() -> None:
    world = World(personas())
    assert await policy(world).visible(["block:secret"]) == set()
    world.spec.acl["bob"].add("r_secret")
    assert await policy(world).visible(["block:secret"]) == {"block:secret"}   # a new request, a new policy


async def test_revalidation_judges_a_node_by_its_own_access_whatever_connector_it_is_in() -> None:
    assert await policy(World(personas())).visible(["block:other"]) == {"block:other"}


async def test_an_empty_spec_hides_everything() -> None:
    assert await policy(World(Spec())).visible(["block:main"]) == set()
