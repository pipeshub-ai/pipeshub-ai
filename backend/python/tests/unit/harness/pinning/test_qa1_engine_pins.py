"""QA-1 PIN-15 and APR-29 on durable engine runs: the org's approval rules across attempts.

An engine asks before every write by default (UX2); the org's rules come on top. Each attempt's tool endpoint
is built by the production ``PipesHubEngineAssembler``, which reads the org's rules today.
"""

from __future__ import annotations

from .support import CREATE, DEPLOYMENT_DENIES_WRITES, NO_RULES, EngineRig

DENIED = "denied"
OFFERED = "deferred"


async def test_pin15_loosened_approvals_do_not_reach_the_next_engine_attempt() -> None:
    rig = EngineRig()
    rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
    first = await rig.attempt()
    assert await first.call(CREATE, title="first") == DENIED

    rig.control.approvals(NO_RULES)
    second = await rig.next_attempt()

    assert await second.call(CREATE, title="second") == DENIED
    assert second.hold.offered == 0


async def test_apr29_a_tightening_reaches_the_next_engine_attempt() -> None:
    rig = EngineRig()
    rig.control.approvals(NO_RULES)
    first = await rig.attempt()
    assert await first.call(CREATE, title="first") == OFFERED

    rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
    second = await rig.next_attempt()

    assert await second.call(CREATE, title="second") == DENIED
