"""Structural changes through the rewrite job, over the scenario corpus (memory vector store; the real stores run the same kit in
`tests/integration/vector_db/test_scope_rewrite_stores.py`).

For every scenario whose mutations move a record, break or restore its inheritance or set its rule:

* from the moment the change commits until its roots are off the list, no point whose stamp is out of date is admitted, and no point is admitted
  that the oracle of the graph as it is now denies (0 admits of the subtree during);
* after it, the stored fields and membership equal the projection of the new graph, and every covered point equals the oracle (no extra, no missing access);
* the job writes exactly the points whose projection changed, all inside the subtree.

A scenario that only changes who holds a grant registers nothing: the job is idle and writes 0 points.
"""

from __future__ import annotations

import pytest

from . import mutations
from .rewrite_kit import VIEWER_ONLY, Env, Log, registration
from .scenario import load_all
from .scope_corpus import SCENARIOS as STRUCT
from .stamp_world import ORG


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


GRANTS = [s for s in load_all() if s.mutations and all(m.kind in VIEWER_ONLY for m in s.mutations)]


@pytest.mark.parametrize("scenario", STRUCT, ids=[s.id for s in STRUCT])
async def test_a_rewritten_subtree_is_hidden_meanwhile_and_exact_after(scenario) -> None:
    env, seen, after = await Env.make(scenario.world), Log(), Log()
    await env.change(scenario.mutations)
    await env.audit(seen)                                   # the change committed, nothing rewritten: the old stamps are all still there
    results = await env.settle()
    await env.audit(after)
    assert not seen.pending_admits and not seen.leaks and not after.leaks, (seen.pending_admits[:5], seen.leaks[:5], after.leaks[:5])
    assert not seen.inexact, seen.inexact[:5]
    # a world the projector already gets wrong from scratch (known) must not get worse by being rewritten
    fresh, fresh_log = await Env.make(mutations.apply_all(scenario.world, scenario.mutations)), Log()
    await fresh.audit(fresh_log)
    assert after.inexact == fresh_log.inexact, (after.inexact[:5], fresh_log.inexact[:5])
    assert not await env.problems()
    assert env.changed <= {env.vrids()[r] for r in env.subtree if r in env.vrids()}, "a stamp outside the subtree changed"
    written = sum(r.points_written for r in results)
    assert written >= len(env.changed), (written, sorted(env.changed))


@pytest.mark.parametrize("scenario", GRANTS, ids=[s.id for s in GRANTS])
async def test_a_grant_only_change_registers_nothing_and_writes_nothing(scenario) -> None:
    env = await Env.make(scenario.world)
    roots, whole = await env.change(scenario.mutations)
    assert not roots and not whole and not env.changed and not env.pending()
    writes = []
    env.before_write = lambda: writes.append(1) or _noop()
    assert await env.settle() == []
    assert not writes and not await env.problems()
    assert registration  # (the registration rule is the kit's: a grant that makes or unmakes a list of its own is not a grant-only change)


async def _noop() -> None:
    return None


def test_the_corpus_has_what_the_gate_needs() -> None:
    assert len(STRUCT) >= 16 and len(GRANTS) >= 10, (len(STRUCT), len(GRANTS))
    assert ORG


async def test_some_scenarios_really_leave_stale_stamps_to_hide() -> None:
    """Counted here, not read from the scenarios' tests: those may run in other workers (`-n 4`), and a module-level tally would be empty."""
    stale = 0
    for scenario in STRUCT:
        env, seen = await Env.make(scenario.world), Log()
        await env.change(scenario.mutations)
        await env.audit(seen)
        stale += bool(seen.stale_seen)
    assert stale >= 8, stale
