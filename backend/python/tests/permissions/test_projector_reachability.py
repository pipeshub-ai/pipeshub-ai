# ruff: noqa: ANN001, ANN201, ANN202, ANN401
"""The three places the projector asks whether a node reaches the app each skip a record-group-level group, as the graph check's walk does.

`seed` is directly granted and OPEN, and its only route to the app goes through the group-level group `top`: a seed (a reader of it reads what
hangs below it). One test per place, so that undoing one of them turns a test red whatever the other two still say."""
from __future__ import annotations

from app.services.permissions.ports import StampNode
from app.services.permissions.projector import (
    MAX_DEPTH,
    _ancestor_facts,
    _facts,
    _seed_ids,
    _up,
)

APP = "A"


def nodes() -> dict[str, StampNode]:
    n = [StampNode(APP, "app", APP),
         StampNode("top", "group", APP, parents=(APP,), inherits=frozenset({APP}), group_level=True),
         StampNode("seed", "record", APP, parents=("top",), inherits=frozenset({"top"}), rule="OPEN", granted=True),
         StampNode("doc", "record", APP, parents=("seed",), inherits=frozenset({"seed"}), rule="RESTRICTED")]
    return {x.id: x for x in n}


def path(ns, start="doc") -> dict[str, int]:
    dist, frontier = {start: 0}, [start]
    for d in range(1, MAX_DEPTH):
        frontier = [p for i in frontier for p in ns[i].parents if p in ns and p not in dist]
        for p in frontier:
            dist[p] = d
        if not frontier:
            break
    return dist


def test_the_node_behind_a_group_level_group_does_not_reach_the_app_in_the_ancestor_facts():
    ns = nodes()
    assert APP in _up(ns, "seed")      # (the route exists in the hierarchy, and the walk cannot take it)
    reach = {a.directly_granted: a.reaches_app for a in _ancestor_facts(ns, "doc", APP)}
    assert reach[True] is False


def test_the_facts_of_a_stamp_take_the_node_behind_a_group_level_group_for_a_seed():
    ns = nodes()
    assert _facts(type("App", (), {"has_group_level": True})(), APP, ns, path(ns))["below_seed_on_path"] is True


def test_the_node_behind_a_group_level_group_is_a_seed():
    ns = nodes()
    assert _seed_ids(APP, ns, path(ns)) == ["seed"]
