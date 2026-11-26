"""recs2 item 4 (R5 on every engine): the flow guard on Codex's real binary (``test_recs2_r5_real_engine_flow.py``'s
cases). Codex speaks the Responses API, so its model calls go through E3's real model gateway on the run's pinned
route, as in E8's controller lane (``test_real_codex_controller.py``, whose stack and gateway this module runs on)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import PinnedRoute
from harness_core.effects.flow import FLOW_RULE
from tests.unit.harness.engines.test_real_codex_controller import (  # noqa: F401 - the lane's fixtures
    codex_broker,
    model_gateway,
    real_stack,
)
from tests.unit.harness.engines.test_recs2_r5_real_engine_flow import (
    admit,
    held_then_refused,
    real_world,
)
from tests.unit.harness.engines.test_sec1_flow_guard import (
    READ_THEN_WRITE,
    RUN,
    creates,
    denied_rules,
    flow,  # noqa: F401 - the fixture
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import ModelGatewayLane, RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

ROUTE_REF = "route-1"


def _route() -> PinnedRoute:
    return PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")


async def test_recs2_r5_codex_holds_a_write_after_a_restricted_read_for_a_person(
    stack: RealStack,
    codex_broker: DockerSandboxBroker,  # noqa: F811
    model_gateway: ModelGatewayLane,  # noqa: F811
    flow: Callable[..., None],  # noqa: F811
) -> None:
    w, kernel = real_world("codex", stack, codex_broker, models=model_gateway, routes=(_route(),))
    await held_then_refused(w, kernel, flow, "codex", route=ROUTE_REF)
    assert {(r.method, r.path) for r in model_gateway.requests} == {("POST", "/v1/responses")}


async def test_recs2_r5_codex_at_deny_refuses_the_write_and_answers(
    stack: RealStack,
    codex_broker: DockerSandboxBroker,  # noqa: F811
    model_gateway: ModelGatewayLane,  # noqa: F811
    flow: Callable[..., None],  # noqa: F811
) -> None:
    flow("deny")
    w, kernel = real_world("codex", stack, codex_broker, models=model_gateway, routes=(_route(),))
    admit(w, "codex", READ_THEN_WRITE, route=ROUTE_REF)
    assert (await w.attempt(RUN, kernel=kernel)).status == "completed"
    assert creates(w) == 0
    assert denied_rules(w) == [FLOW_RULE]
