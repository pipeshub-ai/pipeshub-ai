"""A rewrite killed, or failed, at any step.

* **Killed at every step.** The run is counted once, then killed (a `BaseException`, which nothing may swallow) before each of its graph and
  store calls in turn. Whatever the step: no point is admitted that the oracle denies and no stale stamp is admitted; if the pending list is
  empty the store is exact; and the next run, with nothing killed, ends exact with nothing pending (a hidden subtree is visible again).
* **Transient failures.** Each call may, with probability FAULT_P, raise, or (a write) be acknowledged and lost. A run that raised leaves a root
  pending; a run that returned "done" leaves nothing pending and an exact store; after the faults stop the next runs end exact.
"""

from __future__ import annotations

import os
import random

import pytest

from .rewrite_kit import Env, Killed, Log, Tap
from .scope_corpus import SCENARIOS
from .stamp_world import ORG

FAULT_P = float(os.environ.get("FAULT_P", "0.12"))
PICKS = ["CONF-C-03", "DRIVE-C-07", "LIN-C-05"]
CHOSEN = [s for s in SCENARIOS if s.id in PICKS] or SCENARIOS[:3]


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def prepared(scenario, **kw) -> Env:
    env = await Env.make(scenario.world, **kw)
    await env.change(scenario.mutations)
    return env


async def _nothing(_: str) -> None:
    return None


async def counted(scenario, **kw) -> int:
    env = await prepared(scenario, **kw)

    async def count(_: str) -> None:
        return None

    tap = Tap(env, count)
    await env.settle()
    return len(tap.calls)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize(("share", "chunks"), [(False, 1), (True, 2)])
@pytest.mark.parametrize("scenario", CHOSEN, ids=[s.id for s in CHOSEN])
async def test_a_run_killed_at_any_step_hides_what_is_stale_and_the_next_run_ends_exact(scenario, share, chunks, when) -> None:
    steps = await counted(scenario, share=share, chunks=chunks)
    assert steps > 20, steps
    killed_with_roots = killed_without = 0
    for k in range(1, steps + 1):
        env = await prepared(scenario, share=share, chunks=chunks)
        seen = {"n": 0, "live": True}

        async def die(_: str, k=k, seen=seen) -> None:
            seen["n"] += 1
            if seen["live"] and seen["n"] == k:
                raise Killed

        Tap(env, die if when == "before" else _nothing, die if when == "after" else None)
        with pytest.raises(Killed):
            await env.settle()
        log = Log()
        await env.audit(log)
        tag = (scenario.id, k, steps)
        assert not log.leaks and not log.pending_admits, (tag, log.leaks[:3], log.pending_admits[:3])
        if env.pending():
            killed_with_roots += 1
        else:
            killed_without += 1
            assert not await env.problems(), (tag, "the root came off over an inexact store")
        seen["live"] = False
        await env.settle()
        again = Log()
        await env.audit(again)
        assert not await env.problems() and not again.leaks and not again.pending_admits, (tag, await env.problems())
    # the kills reached both sides of the compare-and-set that takes the roots off (a kill before a call cannot come after the last one)
    assert killed_with_roots and (killed_without or when == "before"), (killed_with_roots, killed_without)


@pytest.mark.parametrize("scenario", CHOSEN, ids=[s.id for s in CHOSEN])
async def test_transient_failures_never_let_a_root_go_over_an_inexact_store(scenario) -> None:
    rng = random.Random(f"faults/{scenario.id}")
    outcomes = {"raised": 0, "done": 0}
    for _ in range(30):
        p = rng.choice([0.004, 0.01, FAULT_P])
        env = await prepared(scenario, share=rng.random() < 0.5, chunks=rng.choice([1, 2]))
        armed = {"on": True}

        async def flaky(name: str, armed=armed, p=p) -> str | None:
            if armed["on"] and rng.random() < p:
                if name == "update_payloads_by_ids" and rng.random() < 0.5:
                    return "drop"
                raise ConnectionError(f"injected: {name}")
            return None

        Tap(env, flaky)
        raised = None
        try:
            for app in env.apps:
                await env.job.run_connector(ORG, app)
        except Exception as exc:
            raised = exc
        armed["on"] = False
        log = Log()
        await env.audit(log)
        assert not log.leaks and not log.pending_admits, (log.leaks[:3], log.pending_admits[:3], raised)
        if raised is not None:
            outcomes["raised"] += 1
            assert env.pending(), ("the run raised and nothing is pending", repr(raised)[:100])
        elif not env.pending():
            outcomes["done"] += 1
            assert not await env.problems(), "done, nothing pending, and the store is not exact"
        await env.settle()
        assert not await env.problems()
    assert outcomes["raised"] and outcomes["done"], outcomes
