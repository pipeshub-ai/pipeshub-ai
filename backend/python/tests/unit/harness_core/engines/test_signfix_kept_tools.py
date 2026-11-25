"""SF-1..SF-5 (SIGNFIX): a signed-in sandbox reaches PipesHub's tools on every attempt and every message.

Found in a manual test: a person signed in with their Claude account and every call Claude Code made to the tool gateway
was refused by the egress proxy ``token_unavailable``. A kept sandbox's labels leave out ``runId`` and ``attemptId`` (a
run's reap filters on them), and those labels were its egress binding's, so the proxy's token source found no attempt to
ask Node a token for. The world's token source here issues only for the attempt a binding names, as Node does, and the
signing adapter lists its tools through the sandbox's egress when it opens, as Claude Code does.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from harness_core.engines.attempt_labels import (
    BOUND_ATTEMPT_LABEL,
    BOUND_RUN_LABEL,
    bound_attempt,
)
from harness_core.engines.controller import KEPT_LABEL
from tests.unit.harness_core.engines.test_controller import (
    GRANTED,
    ORG,
    RUN,
    SESSION,
    task,
)
from tests.unit.harness_core.engines.test_controller_signin import (
    FakeRelay,
    _adapter,  # pyright: ignore[reportPrivateUsage]
    _world,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness_core.engines.test_controller_signin_kept import (
    _run,  # pyright: ignore[reportPrivateUsage]
    _shared,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from tests.unit.harness_core.engines.test_controller import World

TOOLS = tuple(sorted(t.name for t in GRANTED))


def _tool_attempts(world: World) -> list[tuple[str, str] | None]:
    """The attempt each token the tool host received was issued for, in order (what the gateway's claims name)."""
    return [
        world.tokens.attempt_of(r.headers["authorization"].removeprefix("Bearer "))
        for r in world.proxy.seen
        if r.url.startswith(world.host.base)
    ]


async def _turn(world: World, n: int, attempt: int = 1) -> None:
    run = dataclasses.replace(_run(world, n), attempt_id=f"{RUN}-{n}.a{attempt}", fence=attempt)
    outcome = await world.controller.run_turn(run, task(0, "say done"))
    assert outcome.reason == "completed"


async def test_sf1_a_fresh_sign_in_sandbox_reaches_its_tools_with_its_own_attempts_token() -> None:
    world = _shared(_world(relay=FakeRelay()))
    await _turn(world, 1)
    assert _adapter(world).tool_listings == [TOOLS]
    assert world.tokens.refused == 0
    assert _tool_attempts(world) == [(f"{RUN}-1", f"{RUN}-1.a1")]


async def test_sf2_the_next_message_in_the_kept_sandbox_uses_its_own_attempts_token_not_the_last_ones() -> None:
    world = _shared(_world(relay=FakeRelay()))
    await _turn(world, 1)
    world.controller = world.make_controller()
    await _turn(world, 2)
    adapter = _adapter(world)
    assert adapter.calls.count("sign_in") == 1  # the second message reused the sign-in
    assert adapter.tool_listings == [TOOLS, TOOLS]
    # The first message's token is still fresh in the proxy's cache; the gateway must see the second run's.
    assert _tool_attempts(world) == [(f"{RUN}-1", f"{RUN}-1.a1"), (f"{RUN}-2", f"{RUN}-2.a1")]


async def test_sf3_a_redriven_attempt_of_the_same_run_gets_its_own_token() -> None:
    world = _shared(_world(relay=FakeRelay()))
    await _turn(world, 1)
    world.controller = world.make_controller()
    await _turn(world, 1, attempt=2)
    assert _tool_attempts(world) == [(f"{RUN}-1", f"{RUN}-1.a1"), (f"{RUN}-1", f"{RUN}-1.a2")]


async def test_sf4_a_kept_sandbox_names_its_attempt_for_the_binding_only_never_as_a_run_reap_label() -> None:
    world = _shared(_world(relay=FakeRelay()))
    await _turn(world, 1)
    world.controller = world.make_controller()
    await _turn(world, 2)
    [sandbox] = world.broker.sandboxes.values()
    labels = sandbox.spec.labels
    assert "runId" not in labels and "attemptId" not in labels
    assert (labels[BOUND_RUN_LABEL], labels[BOUND_ATTEMPT_LABEL]) == (f"{RUN}-2", f"{RUN}-2.a1")
    binding = world.proxy.bindings.get(sandbox.sandbox_id)
    assert binding is not None and bound_attempt(binding.labels) == (f"{RUN}-2", f"{RUN}-2.a1")
    for run in (f"{RUN}-1", f"{RUN}-2"):
        assert await world.broker.reap(labels={"orgId": ORG, "runId": run}, older_than_s=0) == 0
    assert await world.broker.reap(labels={"orgId": ORG, "sessionId": SESSION, KEPT_LABEL: "1"}, older_than_s=0) == 1


async def test_sf5_a_turns_own_sandbox_still_names_its_attempt_as_before() -> None:
    world = _world(relay=FakeRelay())
    outcome = await world.controller.run_turn(world.run(), task(0, "say done"))
    assert outcome.reason == "completed"
    [(spec, _)] = world.broker.acquired
    assert (spec.labels["runId"], spec.labels["attemptId"]) == (RUN, f"{RUN}.a1")
    assert BOUND_RUN_LABEL not in spec.labels
