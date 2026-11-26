"""The oracle is the single truth for the Container rule: a node stamped `container` is admitted exactly when the record
group (or app) it belongs to is, for every viewer; a node stamped with an exception rule differs for at least one.

A projector that writes per-record scope fields only where a record departs from its container is tested against
these stamps, so a stamp that the oracle contradicts fails here first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .runner import OracleLayer
from .scenario import load_all

if TYPE_CHECKING:
    from .model import World

SCENARIOS = [s for s in load_all() if any(n.stamp for n in s.world.nodes)]
# Every stamped world as written, and every stamped scenario's world after its changes: a change must not make a
# container node depart from its container without its stamp saying so.
WORLDS = {s.world: s.id for s in reversed(SCENARIOS)}
FINAL = {s.id: s.final_world() for s in SCENARIOS if s.mutations}
CASES = [(f"{sid}", w) for w, sid in WORLDS.items()] + [(f"{sid}:after", w) for sid, w in FINAL.items()]


def _container_of(world: World, node_id: str) -> str:
    return world.node(node_id).belongs[0]


def _admits(world: World) -> dict[tuple[str, str], bool]:
    ref = OracleLayer()
    nodes = [n.id for n in world.nodes]
    return {(viewer, node): v.admitted for viewer in world.users()
            for node, v in ref.explain(world, viewer, nodes).items()}


def test_there_are_container_worlds() -> None:
    assert len(WORLDS) >= 4, "slack, gmail, kb and mixed confluence"


@pytest.mark.parametrize("name,world", CASES, ids=[c[0] for c in CASES])
def test_every_node_carries_a_stamp(name: str, world: World) -> None:
    assert [n.id for n in world.nodes if n.kind != "app" and not n.stamp] == []


@pytest.mark.parametrize("name,world", CASES, ids=[c[0] for c in CASES])
def test_top_level_groups_are_the_container(name: str, world: World) -> None:
    top = [n for n in world.nodes if n.kind == "group" and n.belongs and world.node(n.belongs[0]).kind == "app"]
    assert [n.id for n in top if n.stamp != "container"] == []


@pytest.mark.parametrize("name,world", CASES, ids=[c[0] for c in CASES])
def test_container_nodes_follow_their_container_and_exceptions_depart(name: str, world: World) -> None:
    admits = _admits(world)
    for node in (n for n in world.nodes if n.kind != "app" and n.belongs):
        container = _container_of(world, node.id)
        if node.kind == "group" and world.node(container).kind == "app":
            continue  # a top-level record group is the container itself (see the test above)
        if node.deleted or world.node(container).deleted:
            continue  # a deleted node is nobody's
        same = all(admits[(v, node.id)] == admits[(v, container)] for v in world.users())
        if node.stamp == "container":
            assert same, f"{node.id} is stamped container but differs from {container} for some viewer"
        elif node.stamp:
            assert not same, f"{node.id} is stamped {node.stamp} but is just {container}: stamp it container"
