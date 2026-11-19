"""Unit tests (no store) for the generators and the shrinker."""

from __future__ import annotations

import pytest

from . import gen
from .gen.shrink import drop_ids, regression, shrink
from .model import Grant, World
from .scenario import dump_scenarios, load_scenarios, validate_world


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
def test_generated_worlds_are_valid_and_repeatable(kind: str) -> None:
    first = gen.generate(kind, 7, 3)
    assert first == gen.generate(kind, 7, 3)
    assert first != gen.generate(kind, 8, 3)
    for world in first:
        validate_world(world, kind)


async def test_shrink_keeps_only_what_makes_it_fail() -> None:
    world = gen.generate("proptest", 7, 1)[0]
    target = next(g for g in world.grants if g.node in {n.id for n in world.nodes})

    async def fails(w: World) -> bool:
        return target in w.grants

    small = await shrink(world, {target.grantee, target.node}, fails)
    assert small.grants == (target,)
    assert len(small.nodes) < len(world.nodes) and len(small.memberships) == 0


def test_drop_ids_removes_every_reference() -> None:
    world = gen.generate("proptest", 7, 1)[0]
    victim = world.nodes[3].id
    small = drop_ids(world, {victim})
    assert all(victim not in (g.node, g.grantee) for g in small.grants)
    assert all(victim not in [p.id for p in n.parents] + list(n.inherits) for n in small.nodes)
    validate_world(small, "dropped")


def test_regression_scenario_round_trips(tmp_path) -> None:
    world = World(grants=(Grant("org", "x"),))
    from .model import Node, Principal

    world = World(principals=(Principal("u"),), nodes=(Node("A", "app"),))
    scenario = regression("GEN-test", world, "u", "A", "demo")
    path = tmp_path / "r.yaml"
    path.write_text(dump_scenarios([scenario]))
    assert load_scenarios(path)[0].expect == scenario.expect
