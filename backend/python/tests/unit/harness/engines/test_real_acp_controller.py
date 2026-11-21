"""RX-ACP-C (ACP-driver): ``EngineController`` with an ACP engine on its real binary, end to end.

E8's controller lane (``test_real_codex_controller.Lane``) with the release registry's ACP adapter for the shipped
``acp-opencode`` profile (installed as on a dev install: ``PIPESHUB_ACP_PROFILES_UNSIGNED`` reads the
unsigned profile) and E3's real model gateway as the deployment's model route: the controller picks the
chat-completions wire from the profile's capabilities, opencode's ACP server writes the workspace, calls the
PipesHub tool through ``session/new``'s MCP server, is checkpointed, and the next turn resumes it by
``session/load`` in a fresh sandbox.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import RunBudget
from harness_core.budget.types import RunAccountRef
from harness_core.engines.events import UsageReported, turn_id_for
from harness_core.engines.types import FollowUp
from harness_core.engines.usage import AccountUsageLedger
from harness_engines.profile_trust import UNSIGNED_ENV
from harness_engines.release import installed_acp
from tests.unit.harness.engines.real_stack import (
    ModelGatewayLane,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.engines.test_real_acp_conformance import IMAGE, PROFILE
from tests.unit.harness.engines.test_real_codex_controller import TENANT, Lane
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

_E3_PORT: dict[str, int] = {}


@pytest.fixture(scope="module")
def real_stack() -> Iterator[RealStack]:
    """The conftest's stack, with E3's gateway as the proxy's static model upstream."""
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/opencode/build.sh)")

    def upstreams(stack: RealStack) -> Mapping[str, str]:
        _E3_PORT["port"] = _free_port(stack.host_ip)
        return {"EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{_E3_PORT['port']}"})}

    stack = start_stack(upstreams=upstreams)
    yield stack
    stop_stack(stack)


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _E3_PORT["port"]).serving() as lane:
        yield lane


@pytest.fixture
async def acp_broker(stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.acpctl.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-acpc-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=IMAGE)
    yield created
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


@pytest.fixture
def dev_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dev install: the unsigned ACP profile is installed (``harness_engines.profile_trust``)."""
    monkeypatch.setenv(UNSIGNED_ENV, "true")
    assert PROFILE.engine in installed_acp()


def _lane(stack: RealStack, broker: DockerSandboxBroker, models: ModelGatewayLane, **changes: object) -> Lane:
    return Lane(stack, broker, models, version=PROFILE.engine, image=IMAGE, agent="ACP", **changes)  # type: ignore[arg-type]


async def test_rxacpc1_an_acp_turn_end_to_end_then_the_next_turn_loads_its_session(
    stack: RealStack, acp_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane, dev_install: None
) -> None:
    lane = _lane(stack, acp_broker, model_gateway)
    await lane.seed({"data/in.csv": b"a,b\n1,2\n"})
    run = lane.run(1)
    prompt = "RXACPC1-MARKER\nshell mkdir -p out && cp data/in.csv out/report.csv\n" 'tool search {"q": "churn"}\nsay done'
    first = await lane.controller.run_turn(run, lane.task(0, prompt))
    assert (first.reason, first.final_text.strip()) == ("completed", "done")
    assert (await lane.head())["out/report.csv"] == b"a,b\n1,2\n"
    assert [(p.path, p.lifecycle) for p in first.published] == [("report.csv", "draft")]
    assert first.checkpoint is not None and lane.sink.checkpoints[-1].blob == first.checkpoint
    # The controller gave the agent the chat-completions wire; every model call went through E3, none refused.
    assert {(r.method, r.path) for r in model_gateway.requests} == {("POST", "/v1/chat/completions")}
    assert model_gateway.refused == []
    assert sum(lane.gateways[1].executions.values()) == 1
    second = await lane.controller.run_turn(
        lane.run(2, restore=first.checkpoint), FollowUp(turn_id_for(lane.run_id, 1), "say again")
    )
    assert (second.reason, second.restored, second.restore_issue) == ("completed", "state", None)
    last = lane.gateways[1].model_calls[-1]
    assert any("RXACPC1-MARKER" in m.content for m in last.messages if m.role == "user")


async def test_rxacpc2_the_model_gateways_budget_refusal_ends_the_turn_as_a_limit(
    stack: RealStack, acp_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane, dev_install: None
) -> None:
    lane = _lane(stack, acp_broker, model_gateway, budget=RunBudget(max_cost_usd=None, max_tokens=1))
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, 'tool search {"q": 1}\nsay never'))
    assert (outcome.reason, outcome.limit) == ("limit", "budget")


async def test_rxacpc3_cf13_the_turns_usage_is_the_gateway_ledgers_though_opencode_reports_its_last_call(
    stack: RealStack, acp_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane, dev_install: None
) -> None:
    """CF-13 on the real binary (OWD-8): opencode over ACP answers ``session/prompt`` with its last call's usage only;
    the controller's turn usage is the model gateway's ledger, every call of the turn counted."""
    lane = _lane(stack, acp_broker, model_gateway)
    ledger = AccountUsageLedger(model_gateway.ledger, RunAccountRef(TENANT, lane.run_id))
    run = dataclasses.replace(lane.run(1), usage=ledger)
    outcome = await lane.controller.run_turn(run, lane.task(0, 'tool search {"q": "usage"}\nsay counted'))
    assert outcome.reason == "completed"
    assert len(lane.gateways[1].model_calls) >= 2
    used = (await ledger.read()).used
    assert outcome.usage_source == "ledger"
    assert (outcome.input_tokens, outcome.output_tokens) == (used.input_tokens, used.output_tokens)
    billed = lane.gateways[1].billed
    assert (used.input_tokens, used.output_tokens) == (
        sum(u.input_tokens for u in billed),
        sum(u.output_tokens for u in billed),
    )
    reported = [e for _, e in lane.sink.events if isinstance(e, UsageReported)]
    assert sum(e.input_tokens for e in reported) < used.input_tokens, "opencode reported its last call only"
