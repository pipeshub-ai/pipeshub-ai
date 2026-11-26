"""recs2 item 3 (R7) with the coding engine on Codex's real binary: the deep-mode hand-off of
``test_recs2_r7_code_task.py``, the child's model calls through E3's real model gateway on its pinned route (E8's
controller lane, whose stack and gateway this module runs on). Claude Code's is ``test_recs2_real_claude_code.py``."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from harness_core.access.types import PinnedRoute
from harness_core.testing.engines.durable import EngineWorld
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.r7_rig import (
    PARENT,
    R7Rig,
    engine_authority,
    orchestrator,
)
from tests.unit.harness.engines.real_engine_world import RealEngineAssembler
from tests.unit.harness.engines.test_real_codex_controller import (  # noqa: F401 - the lane's fixtures
    codex_broker,
    model_gateway,
    real_stack,
)
from tests.unit.harness.engines.test_recs2_r7_code_task import (
    ANSWER,
    GOAL,
    _assert_parent_got_it_back,  # pyright: ignore[reportPrivateUsage]
    drive,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import ModelGatewayLane, RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def test_recs2_r7_a_deep_root_run_gets_a_real_codex_sessions_result_and_files_back(
    stack: RealStack,
    codex_broker: DockerSandboxBroker,  # noqa: F811
    model_gateway: ModelGatewayLane,  # noqa: F811
) -> None:
    route = PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")
    world = EngineWorld(engine_authority=engine_authority("codex", "route-1"), routes=(route,))
    child = RealEngineAssembler(
        world, stack=stack, broker=codex_broker, kind="codex", models=model_gateway,
        content=lambda: PipesHubContentStore(world.journal.content),  # type: ignore[attr-defined]
    )
    rig = R7Rig(world, orchestrator(GOAL, ANSWER), engine_assembler=child, broker=codex_broker)
    seen = await drive(rig)
    _assert_parent_got_it_back(rig, seen["child"])
    checkpointed = [e for e in rig.events(seen["child"]) if e.type == "engine.checkpointed"]
    assert {e.data["engine"]["kind"] for e in checkpointed} == {"codex"}, checkpointed  # type: ignore[index]
    merged = [e.data for e in rig.events() if e.type == "workspace.committed" and e.run_id == PARENT]
    assert merged and "/report.md" in merged[-1]["changed"], merged
    assert {(r.method, r.path) for r in model_gateway.requests} == {("POST", "/v1/responses")}
