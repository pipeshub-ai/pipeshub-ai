"""A folder breaks inheritance and takes a list of its own, and one restores it: the nearest scope of everything below it moves from one id to another.
Through the rewrite job: nothing is admitted that the oracle denies while the subtree is rewritten, and afterwards the stored fields equal the graph's
projection and every covered point equals the oracle, in both directions (no extra access, no missing access).
"""

from __future__ import annotations

import pytest

from .model import Mutation as M
from .rewrite_kit import Env, Log
from .scenario import WorldStore

TREE = WorldStore().world("golden/sharepoint_tree")
CASES = {
    "break": (M("inherit_set", "folder:Specs", "folder:Eng", "false"), M("grant_added", "folder:Specs", "g:ops")),
    "restore": (M("inherit_set", "folder:Plans", "folder:Eng", "true"), M("grant_removed", "folder:Plans", "g:ops")),
}


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


@pytest.mark.parametrize("case", CASES)
async def test_a_broken_or_restored_inheritance_is_hidden_meanwhile_and_exact_after(case: str) -> None:
    env, seen, after = await Env.make(TREE), Log(), Log()
    await env.change(CASES[case])
    env.before_write = lambda: env.audit(seen)                 # before every write request of the store
    results = await env.settle()
    await env.audit(after)
    assert env.changed and sum(r.points_written for r in results) >= len(env.changed)
    assert not seen.leaks and not seen.pending_admits, (seen.leaks[:4], seen.pending_admits[:4])
    assert not after.leaks and not after.inexact, (after.leaks[:4], after.inexact[:4])
    assert not await env.problems()                            # every connector current again, every point exact
