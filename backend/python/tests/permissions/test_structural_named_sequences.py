"""Named structural sequences on the golden worlds, every step held to the oracle of the graph as it is at that step: no leak, ever, no stale
stamp admitted, and exact once settled. Each runs batched (all changes committed, then one rewrite) and midflight (each later change committed
after a page of the rewrite read the graph and before it wrote, so the write is refused as old and the page is read again), on 1- and 3-chunk
points and with shared virtual records.
"""

from __future__ import annotations

import pytest

from .model import Mutation as M
from .rewrite_kit import Env, Log, play
from .scenario import WorldStore
from .test_scope_projector import retyped

W = WorldStore()


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


# (id, world, app type override, changes in order, what it attacks)
CASES = [
    ("SEQ-01", "golden/confluence_hr", None,
     [M("moved", "page:Onboarding", "page:Compensation"), M("moved", "page:Compensation", "page:Handbook")],
     "a page is moved into a restricted parent, then that parent is moved: the second root is an ancestor of the first"),
    ("SEQ-02", "golden/confluence_hr", None,
     [M("moved", "page:Handbook", "space:ALL", "space:ALL"), M("rule_set", "page:Onboarding", "", "RESTRICTED"), M("moved", "page:Onboarding", "space:HR", "space:HR")],
     "cross-space move, a page restriction added below it while it is rewritten, then the restricted page moves back"),
    ("SEQ-03", "golden/sharepoint_tree", None,
     [M("inherit_set", "folder:Specs", "folder:Eng", "false"), M("grant_added", "folder:Specs", "g:ops"), M("inherit_set", "folder:Specs", "folder:Eng", "true"),
      M("grant_removed", "folder:Specs", "g:ops")],
     "break inheritance (first direct grant), restore it (last direct grant removed) while the first rewrite runs: a flip flop on one root"),
    ("SEQ-04", "golden/sharepoint_tree", None,
     [M("moved", "folder:Specs", "folder:Ops"), M("moved", "folder:Deep", "library:Docs"), M("moved", "deep-a", "folder:Plans")],
     "an ancestor, a descendant of it and a leaf of that descendant all move: three nested roots"),
    ("SEQ-05", "golden/sharepoint_tree", "DRIVE WORKSPACE",
     [M("inherit_set", "folder:Specs", "folder:Eng", "false"), M("moved", "folder:Specs", "folder:Ops"), M("grant_added", "spec-a", "ann")],
     "the same tree under the Union rule (limited access on, then moved, then a file gets its first grant)"),
    ("SEQ-06", "golden/sharepoint_tree", "CONFLUENCE",
     [M("rule_set", "folder:Eng", "", "RESTRICTED"), M("moved", "folder:Plans", "folder:Ops"), M("rule_set", "folder:Eng", "", "")],
     "the same tree under the Intersection rule: a restriction added on an ancestor and lifted again while a descendant moves out"),
    ("SEQ-07", "golden/onedrive_drive", None,
     [M("inherit_set", "folder:Shared", "drive:Ann", "true"), M("moved", "f1", "folder:Private"), M("grant_removed", "folder:Shared", "g:team"),
      M("grant_removed", "folder:Shared", "ann")],
     "a synthetic restore, a file moves into the private folder, the folder loses its last grants (flip) while it is rewritten"),
    ("SEQ-08", "golden/drive_q4", "DRIVE WORKSPACE",
     [M("moved", "plan", "folder:Q4-Board"), M("moved", "folder:Q4-Board", "folder:Q4-Eng"), M("grant_removed", "folder:Q4-Board", "ceo")],
     "a file into the limited Board folder, the Board folder then under Eng, a Board grant removed: seeds and exclusions change together"),
    ("SEQ-09", "golden/confluence_hr", None,
     [M("moved", "page:2027-bands", "page:Handbook"), M("moved", "page:Compensation", "page:Onboarding"), M("moved", "page:2027-bands", "space:HR", "space:HR")],
     "a restricted child leaves its restricted parent and the parent then moves under an open page: the child's old path loses the parent"),
]


@pytest.mark.parametrize("mode", ["batched", "midflight"])
@pytest.mark.parametrize(("share", "chunks"), [(False, 1), (False, 3), (True, 2)])
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
async def test_adversarial(case, share, chunks, mode) -> None:
    sid, ref, app_type, muts, _ = case
    world = W.world(ref)
    if app_type:
        world = retyped(world, app_type)
    env, seen = await Env.make(world, share=share, chunks=chunks), Log()
    await play(env, muts, mode, seen)
    await env.audit(seen)
    assert not seen.leaks and not seen.pending_admits, (sid, mode, seen.leaks[:3], seen.pending_admits[:3])
    assert not await env.problems(), sid
    fresh, base = await Env.make(env.world, share=share, chunks=chunks), Log()
    await fresh.audit(base)
    assert base.inexact or not seen.inexact, (sid, seen.inexact[:3])  # a world the projector gets right from scratch stays right
    assert seen.audits >= 3
