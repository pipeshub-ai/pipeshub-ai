# ruff: noqa: ANN401
"""A page moved to another space: its point is container-stamped, so its `recordGroupIds` is all that says who reads it. If the graph read of its
virtual record is incomplete during the rewrite (both providers' `get_document` return None on a transient error, which is what `complete` is
for), the old space still reads the page: the run raises, the root stays pending and hidden, and the next run with a graph that answers
completes exactly. A membership that cannot be read must not blank the old one either.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.services.vector_db import scope_rewrite
from app.services.vector_db.scope_rewrite import RewriteIncomplete

from .model import Mutation as M
from .rewrite_kit import Env, Log
from .scenario import WorldStore
from .stamp_world import ORG


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


@pytest.mark.parametrize("fails", [1, 100])
async def test_a_membership_read_that_is_incomplete_does_not_let_the_root_go(monkeypatch: pytest.MonkeyPatch, fails: int) -> None:
    env = await Env.make(WorldStore().world("golden/confluence_hr"))
    await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
    real = scope_rewrite.resolve_virtual_record_state
    left = {"n": fails}

    async def flaky(graph: Any, vrid: str, *a: Any, **k: Any) -> Any:
        state = await real(graph, vrid, *a, **k)
        if left["n"] > 0 and vrid.endswith("v-page:Handbook"):
            left["n"] -= 1
            return replace(state, complete=False)
        return state

    monkeypatch.setattr(scope_rewrite, "resolve_virtual_record_state", flaky)
    before = {p.id: list(p.payload["recordGroupIds"]) for p in (await env.vdb.retrieve_points("rewrite", env.loaded.points["w:v-page:Handbook"]))}
    for _ in range(min(fails, 3)):
        with pytest.raises(RewriteIncomplete):
            await env.job.run_connector(ORG, "w:app")
        assert env.pending(), "the root was removed over a point whose membership was never refreshed: the old space still reads the page"
        seen = Log()
        await env.audit(seen)
        assert not seen.leaks and not seen.pending_admits, (seen.leaks[:4], seen.pending_admits[:4])
        after = {p.id: list(p.payload["recordGroupIds"]) for p in (await env.vdb.retrieve_points("rewrite", env.loaded.points["w:v-page:Handbook"]))}
        assert after == before, "an unreadable membership blanked or changed the old one"
    left["n"] = 0  # the graph answers again
    res = await env.job.run_connector(ORG, "w:app")
    seen = Log()
    await env.audit(seen)
    assert res.state == "done" and not env.pending() and not await env.problems() and not seen.leaks and not seen.inexact, (res.state, env.pending())
