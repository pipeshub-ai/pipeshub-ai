"""recs2 items 3 and 4 on Claude Code's real binary: the deep-mode ``code_task`` hand-off (R7) and the flow guard on a
read-then-write (R5), as ``test_recs2_r7_code_task.py`` and ``test_recs2_r5_real_engine_flow.py`` run them on opencode.
Claude Code speaks Messages; its model calls go through E3's real model gateway on the run's pinned route (E9's
controller lane, whose stack and gateway this module runs on). Nothing is spent: the gateway calls the scripted model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from harness_core.access.types import PinnedRoute
from harness_core.effects.flow import FLOW_RULE
from harness_core.testing.engines.durable import EngineWorld
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.r7_rig import (
    PARENT,
    R7Rig,
    engine_authority,
    orchestrator,
)
from tests.unit.harness.engines.real_engine_world import RealEngineAssembler
from tests.unit.harness.engines.test_real_claude_code_controller import (  # noqa: F401 - the lane's fixtures
    claude_broker,
    model_gateway,
    real_stack,
)
from tests.unit.harness.engines.test_recs2_r5_real_engine_flow import (
    admit,
    held_then_refused,
    real_world,
)
from tests.unit.harness.engines.test_recs2_r7_code_task import (
    ANSWER,
    GOAL,
    _assert_parent_got_it_back,  # pyright: ignore[reportPrivateUsage]
    drive,
)
from tests.unit.harness.engines.test_sec1_flow_guard import (
    READ_THEN_WRITE,
    RUN,
    creates,
    denied_rules,
    flow,  # noqa: F401 - the fixture
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import ModelGatewayLane, RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

KIND = "claude_code"
ROUTE_REF = "route-1"


def _route() -> PinnedRoute:
    return PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")


async def test_recs2_r5_claude_code_holds_a_write_after_a_restricted_read_for_a_person(
    stack: RealStack,
    claude_broker: DockerSandboxBroker,  # noqa: F811
    model_gateway: ModelGatewayLane,  # noqa: F811
    flow: Callable[..., None],  # noqa: F811
) -> None:
    world, kernel = real_world(KIND, stack, claude_broker, models=model_gateway, routes=(_route(),))
    await held_then_refused(world, kernel, flow, KIND, route=ROUTE_REF)


async def test_recs2_r5_claude_code_at_deny_refuses_the_write_and_answers(
    stack: RealStack,
    claude_broker: DockerSandboxBroker,  # noqa: F811
    model_gateway: ModelGatewayLane,  # noqa: F811
    flow: Callable[..., None],  # noqa: F811
) -> None:
    flow("deny")
    world, kernel = real_world(KIND, stack, claude_broker, models=model_gateway, routes=(_route(),))
    admit(world, KIND, READ_THEN_WRITE, route=ROUTE_REF)
    assert (await world.attempt(RUN, kernel=kernel)).status == "completed"
    assert creates(world) == 0
    assert denied_rules(world) == [FLOW_RULE]


@pytest.fixture
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


@pytest.mark.usefixtures("_deployment")
async def test_recs2_r7_a_deep_root_run_gets_a_real_claude_code_sessions_result_and_files_back(
    stack: RealStack,
    claude_broker: DockerSandboxBroker,  # noqa: F811
    model_gateway: ModelGatewayLane,  # noqa: F811
) -> None:
    world = EngineWorld(engine_authority=engine_authority(KIND, ROUTE_REF), routes=(_route(),))
    child = RealEngineAssembler(
        world, stack=stack, broker=claude_broker, kind=KIND, models=model_gateway,
        content=lambda: PipesHubContentStore(world.journal.content),  # type: ignore[attr-defined]
    )
    rig = R7Rig(world, orchestrator(GOAL, ANSWER), engine_assembler=child, broker=claude_broker)
    seen = await drive(rig)
    _assert_parent_got_it_back(rig, seen["child"])
    checkpointed = [e for e in rig.events(seen["child"]) if e.type == "engine.checkpointed"]
    assert {e.data["engine"]["kind"] for e in checkpointed} == {KIND}, checkpointed  # type: ignore[index]
    merged = [e.data for e in rig.events() if e.type == "workspace.committed" and e.run_id == PARENT]
    assert merged and "/report.md" in merged[-1]["changed"], merged
