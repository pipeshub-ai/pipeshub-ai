"""recs2 item 4 (R5 on every engine): the flow guard on opencode's real binary, as SEC1 tries it on the scripted engine.

The engine reads a restricted record (``fetch_record``, which journals what it read as PipesHub's citation sink does)
and then calls an external write (``create``) with the record's text, through the run's real tool endpoint, effect
runner, approvals and journal with PipesHub's session ledger and flow rule (``test_sec1_flow_guard.py``'s world). Only
the engine differs: the release image in the Docker broker's sandbox behind the real egress proxy and tool gateway.
With the rule at ``ask`` the write waits for a person and nothing is written; at ``deny`` it is refused and the run
answers. Codex's case is ``test_recs2_r5_real_codex_flow.py`` (it needs E3's model gateway); Claude Code's rows are
``test_qa31_claude_code_rows.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.effects.flow import FLOW_RULE
from harness_core.engines.profiles import profile_from_wire
from harness_core.testing.engines.durable import engine_run_spec
from tests.unit.harness.engines.real_engine_world import (
    RealEngineAssembler,
    real_profile,
)
from tests.unit.harness.engines.test_sec1_flow_guard import (
    READ_THEN_WRITE,
    RUN,
    creates,
    denied_rules,
    flow,  # noqa: F401 - the fixture
    world,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.testing.engines.durable import EngineWorld
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]


def real_world(kind: str, stack: Any, broker: Any, **lane: Any) -> tuple[EngineWorld, Any]:  # noqa: ANN401
    """SEC1's world with its engine on ``kind``'s binary; returns it and the kernel that runs it."""
    w = world(**({"routes": lane.pop("routes")} if "routes" in lane else {}))
    kernel = w.kernel(assembler=RealEngineAssembler(w, stack=stack, broker=broker, kind=kind, **lane), broker=broker)
    return w, kernel


def admit(w: EngineWorld, kind: str, prompt: str, route: str = "route-azure-gpt") -> None:
    from harness_core.access.types import AccessRouteRef

    profile = profile_from_wire(real_profile(kind, route), access=AccessRouteRef(route, "d1"))
    w.admit(RUN, prompt, spec=engine_run_spec(profile, tools=w.tools))


async def held_then_refused(w: EngineWorld, kernel: Any, turn_on: Callable[..., None], kind: str, **admit_with: str) -> None:  # noqa: ANN401
    turn_on("ask")
    admit(w, kind, READ_THEN_WRITE.replace("say done", "say waiting"), **admit_with)
    disposition = await w.attempt(RUN, kernel=kernel)
    assert disposition.status == "suspended", disposition
    assert disposition.reason is not None and disposition.reason.startswith("ix.approve."), disposition
    explained = [e.data for e in w.events(RUN) if e.type == "interaction.explained"]
    assert explained and explained[0].get("rule") == FLOW_RULE, explained
    assert creates(w) == 0, "nothing was written while it waited for a person"


async def test_recs2_r5_opencode_holds_a_write_after_a_restricted_read_for_a_person(
    stack: RealStack, broker: DockerSandboxBroker, flow: Callable[..., None],  # noqa: F811
) -> None:
    w, kernel = real_world("opencode", stack, broker)
    await held_then_refused(w, kernel, flow, "opencode")


async def test_recs2_r5_opencode_at_deny_refuses_the_write_and_answers(
    stack: RealStack, broker: DockerSandboxBroker, flow: Callable[..., None],  # noqa: F811
) -> None:
    flow("deny")
    w, kernel = real_world("opencode", stack, broker)
    admit(w, "opencode", READ_THEN_WRITE)
    assert (await w.attempt(RUN, kernel=kernel)).status == "completed"
    assert creates(w) == 0
    assert denied_rules(w) == [FLOW_RULE]
