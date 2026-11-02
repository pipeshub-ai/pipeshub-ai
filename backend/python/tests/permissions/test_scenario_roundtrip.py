"""The YAML DSL round-trips: load, dump, load again gives the same scenarios and worlds."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from . import mutations
from .model import Grant, Membership, Mutation, Node, Parent, Principal, World
from .scenario import (
    SCENARIOS_ROOT,
    WorldStore,
    all_scenario_files,
    dump_scenarios,
    dump_world,
    load_scenarios,
    world_from_data,
)

if TYPE_CHECKING:
    from pathlib import Path


def _world_files() -> list[Path]:
    return sorted((SCENARIOS_ROOT / "golden").glob("*.yaml"))


@pytest.mark.parametrize("path", all_scenario_files(), ids=lambda p: str(p.relative_to(SCENARIOS_ROOT)))
def test_scenario_file_round_trips(path: Path, tmp_path: Path) -> None:
    store = WorldStore()
    first = load_scenarios(path, store)
    copy = tmp_path / "again.yaml"
    copy.write_text(dump_scenarios(first))
    assert load_scenarios(copy, store) == first


@pytest.mark.parametrize("path", _world_files(), ids=lambda p: p.stem)
def test_world_file_round_trips(path: Path) -> None:
    world = world_from_data(yaml.safe_load(path.read_text()))
    assert world_from_data(yaml.safe_load(dump_world(world))) == world


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        world_from_data({"nodes": [{"id": "a", "kind": "app", "colour": "red"}]})


def test_dangling_ids_are_rejected(tmp_path: Path) -> None:
    from .scenario import validate_world

    with pytest.raises(ValueError, match="unknown ids"):
        validate_world(World(grants=(Grant("ghost", "nowhere"),)), "w")


def _small() -> World:
    return World(
        principals=(Principal("u"), Principal("g", "group")),
        nodes=(Node("a", "app"), Node("n", "record", "a", "STRICT", (Parent("a"),), ("a",))),
        grants=(Grant("g", "n"),),
        memberships=(Membership("u", "g"),),
    )


def test_mutations_edit_edges_and_leave_the_original() -> None:
    world = _small()
    after = mutations.apply(world, Mutation("grant_removed", "n", "g"))
    assert after.grants == () and world.grants != ()
    assert mutations.apply(world, Mutation("rule_set", "n", value="OPEN")).node("n").rule == "OPEN"
    moved = mutations.apply(world, Mutation("moved", "n", "a"))
    assert moved.node("n").parents == (Parent("a"),)
    with pytest.raises(ValueError):
        mutations.apply(world, Mutation("grant_removed", "n", "nobody"))
    with pytest.raises(ValueError):
        mutations.apply(world, Mutation("nonsense"))
