# ruff: noqa: ANN201
"""A move that DETACHES a subtree from the app (the new parent has no hierarchy route to it) changes the projection of descendants whose stamp
does not depend on the root: a Union descendant that does not inherit carries a `pathIds` that stops below the root, so the pending hide cannot
name it. The rewrite must not release the connector over such a point: it leaves the connector to the full pass (nothing is read from its
stamps meanwhile, which the audit confirms), and the pass then makes it exact.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from .model import Mutation as M
from .model import Node
from .rewrite_kit import Env, Log
from .scenario import WorldStore
from .stamp_world import ORG
from .test_scope_projector import retyped


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


def drive_with_an_orphan(app_type: str):
    world = retyped(WorldStore().world("golden/drive_q4"), app_type)
    orphan = Node("orphan", "record", "app", rule="OPEN")      # a record of the app that no hierarchy edge reaches
    return replace(world, nodes=(*world.nodes, orphan))


@pytest.mark.parametrize("moved", ["folder:Q4-Eng", "folder:Q4-Board", "folder:Q4-Ops"])
@pytest.mark.parametrize("app_type", ["DRIVE WORKSPACE", "SHAREPOINT", "CONFLUENCE"])
async def test_a_move_that_detaches_a_subtree_is_hidden_while_pending(app_type: str, moved: str) -> None:
    env, seen = await Env.make(drive_with_an_orphan(app_type), chunks=2), Log()
    await env.change((M("moved", moved, "orphan"),))
    await env.audit(seen)
    first = await env.job.run_connector(ORG, "w:app")
    states = [first.state]
    await env.audit(seen)                                   # after one run: released, or left to the full pass
    states += [r.state for r in await env.settle()]
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits, (app_type, moved, states, seen.leaks[:3], seen.pending_admits[:3])
    assert not await env.problems(), (app_type, moved, states)
    print(f"\n{app_type} {moved}: {states}")


async def test_a_union_descendant_that_does_not_name_the_root_sends_the_connector_to_a_full_pass() -> None:
    results = {}
    for moved in ("folder:Q4-Eng", "folder:Q4-Board", "folder:Q4-Ops", "folder:Q4-Legal"):
        env = await Env.make(drive_with_an_orphan("DRIVE WORKSPACE"))
        await env.change((M("moved", moved, "orphan"),))
        results[moved] = (await env.job.run_connector(ORG, "w:app")).state
    assert "full_pass" in results.values(), results
