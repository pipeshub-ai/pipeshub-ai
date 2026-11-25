"""Revalidate: batch boundaries, mixed kinds, junk, test nodes, other orgs.
"""
import pytest

from tests.unit.agents.actions.code_graph.conftest import CONN
from tests.unit.modules.graph_view.world import Spec, World, revalidate


def big_world(n: int) -> tuple[World, list[str]]:
    spec = Spec(records={f"r{i}": CONN for i in range(n)}, blocks={f"b{i}": f"r{i}" for i in range(n)})
    spec.test_records = {f"r{i}" for i in range(n) if i % 7 == 0}
    spec.acl = {"u": {f"r{i}" for i in range(n) if i % 3 != 0}}
    return World(spec), [f"b{i}" for i in range(n)]


@pytest.mark.parametrize("n", [1, 499, 500, 501, 1000, 1001, 1500, 5000])
@pytest.mark.parametrize("tests", [False, True])
def test_every_batch_boundary_answers_what_the_rule_says(n, tests) -> None:
    world, keys = big_world(n)
    ids = [f"block:{k}" for k in keys] + [f"record:r{i}" for i in range(n)]
    ids = ids[:5000]
    res = revalidate(world, "u", ids, includeTests=tests)
    assert res.status_code == 200
    spec = world.spec

    def sees(i: str) -> bool:
        kind, _, key = i.partition(":")
        if kind != "block":                          # a record is no node, readable or not
            return False
        rid = spec.blocks[key]
        return rid in spec.acl["u"] and (tests or rid not in spec.test_records)

    assert res.json()["visible"] == [i for i in ids if sees(i)]


def test_junk_ids_are_absent_not_errors() -> None:
    world, _ = big_world(5)
    junk = ["", "block:", "record:", ":", "block", "BLOCK:b1", "block:b1:extra", " block:b1", "block:b1 ", "block:\x00",
            "block:" + "x" * 190, "record:../../etc", "folder:r1", "block:b1\n", "block:%62%31", "block:é", "‮block:b1"]
    res = revalidate(world, "u", ["block:b1"] + [j for j in junk if j])
    assert res.status_code == 200 and res.json() == {"visible": ["block:b1"]}


def test_duplicates_are_answered_once_in_first_seen_order() -> None:
    world, _ = big_world(5)
    res = revalidate(world, "u", ["block:b2", "block:b1", "block:b2", "block:b1", "block:b4"])
    assert res.json()["visible"] == ["block:b2", "block:b1", "block:b4"]


def test_a_block_of_another_org_and_a_record_of_another_org_are_absent() -> None:
    spec = Spec(records={"r1": CONN, "r2": CONN}, blocks={"b1": "r1", "b2": "r2"}, orgs={"b2": "org-elsewhere"})
    spec.acl = {"u": {"r1", "r2"}}
    assert revalidate(World(spec), "u", ["block:b1", "block:b2"]).json() == {"visible": ["block:b1"]}
