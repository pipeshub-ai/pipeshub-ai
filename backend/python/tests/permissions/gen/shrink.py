"""Shrink a failing world to a minimal one and file it as a regression scenario.

Greedy delta debugging over the world's parts: try removing half of a part's
items, then a quarter, down to one, keeping any removal that still fails. The
viewer and the node under test are never removed. Nothing here knows about
stores: `fails` is any async predicate on a world.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, TypeVar

from ..model import ORG_ID, Node, World
from ..runner import OracleLayer
from ..scenario import Expect, Scenario, Verdict, dump_scenarios

if TYPE_CHECKING:
    from pathlib import Path

    from ..graph import Graph

T = TypeVar("T")
Fails = Callable[[World], Awaitable[bool]]


def drop_ids(world: World, gone: set[str]) -> World:
    """Remove nodes or principals and every edge that touches them."""
    def keep_node(n: Node) -> Node:
        return replace(
            n,
            parents=tuple(p for p in n.parents if p.id not in gone),
            inherits=tuple(i for i in n.inherits if i not in gone),
            belongs=tuple(b for b in n.belongs if b not in gone),
        )

    return World(
        principals=tuple(p for p in world.principals if p.id not in gone),
        nodes=tuple(keep_node(n) for n in world.nodes if n.id not in gone),
        grants=tuple(g for g in world.grants if not {g.grantee, g.node} & gone),
        memberships=tuple(m for m in world.memberships if not {m.member, m.group} & gone),
        gates=tuple(g for g in world.gates if not {g.who, g.app} & gone),
        links=tuple(k for k in world.links if not {k.user, k.source, k.connector} & gone),
    )


async def _ddmin(items: Sequence[T], rebuild: Callable[[list[T]], World], fails: Fails) -> list[T]:
    current = list(items)
    chunk = max(1, len(current) // 2)
    while current and chunk >= 1:
        index, shrunk = 0, False
        while index < len(current):
            trial = current[:index] + current[index + chunk:]
            if await fails(rebuild(trial)):
                current, shrunk = trial, True
            else:
                index += chunk
        if not shrunk or chunk == 1:
            chunk //= 2
    return current


async def shrink(world: World, keep: set[str], fails: Fails) -> World:
    """The smallest world found that still fails, always containing the ids in `keep`."""
    keep = keep | {ORG_ID}
    parts: list[tuple[str, Callable[[World], Sequence[object]], Callable[[World, list], World]]] = [
        ("grants", lambda w: w.grants, lambda w, xs: replace(w, grants=tuple(xs))),
        ("memberships", lambda w: w.memberships, lambda w, xs: replace(w, memberships=tuple(xs))),
        ("gates", lambda w: w.gates, lambda w, xs: replace(w, gates=tuple(xs))),
        ("links", lambda w: w.links, lambda w, xs: replace(w, links=tuple(xs))),
    ]
    for _, get, put in parts:
        base = world
        world = put(base, await _ddmin(get(base), lambda xs, b=base, p=put: p(b, xs), fails))
    for kind in ("nodes", "principals"):
        ids = [x.id for x in getattr(world, kind) if x.id not in keep]
        base = world
        kept = await _ddmin(ids, lambda xs, b=base, i=ids: drop_ids(b, set(i) - set(xs)), fails)
        world = drop_ids(base, set(ids) - set(kept))
    return world


def regression(scenario_id: str, world: World, viewer: str, node: str, note: str,
               shape: Callable[[Graph], Graph] | None = None) -> Scenario:
    """A scenario that expects the oracle's verdict on the failing pair, as the failing layer's store holds the world
    (`shape`): the verdict that failed is the one filed."""
    verdict = (OracleLayer(shape) if shape else OracleLayer()).explain(world, viewer, [node])[node]
    return Scenario(
        id=scenario_id, title=f"Generated: {note}", world=world, source="generator",
        rule="regression", comment=f"The oracle says {viewer} -> {node}: {'admit' if verdict.admitted else 'deny'} ({verdict.reason}). {note}",
        expect=Expect(verdicts=(Verdict(viewer, node, verdict.admitted),)), tags=("regression", "generated"))


def write_regression(directory: Path, scenario: Scenario) -> Path:
    path = directory / f"{scenario.id}.yaml"
    path.write_text(dump_scenarios([scenario]))
    return path
