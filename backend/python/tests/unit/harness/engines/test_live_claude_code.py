"""LV-1 (E9): Claude Code in its image writes a file for a small task, its model live on Azure OpenAI.

E8's controller lane (``Lane``) on the release registry's Claude Code adapter, behind the real proxy,
with the deployment's model route pointed at the model gateway as ``app.model_gateway_main`` wires it
(A1's guarded resolver, the shipped signed vendor rules, Node's grants and ledger through a fake of its
internal routes) instead of the scripted model. The gateway reaches the Azure deployment on the
Responses API (configured as a reasoning model, E3c FB-3).

Runs only when the Azure test variables are set (in the environment, or in the dotenv file
``HARNESS_LIVE_ENV_FILE`` names, exported for this test only). Values are never printed and are
asserted absent from the logs. The run's pinned budget is USD 0.5.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, override

import pytest
from aiohttp.test_utils import TestServer

from app.model_gateway_main import create_gateway
from harness_core.access.types import PinnedRoute, route_digest
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.tree import usd_micros
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.clock import SystemClock
from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.events import UsageReported
from harness_core.gateway.model_server import ModelGatewayServer
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_engines.release import release_allowlist
from tests.unit.harness.access.budget_node import BudgetNode
from tests.unit.harness.access.support import StackConfig, published
from tests.unit.harness.engines.live_lane import (
    MODEL_KEY,
    LiveNode,
    _ai_models,  # pyright: ignore[reportPrivateUsage]
    _model,  # pyright: ignore[reportPrivateUsage]
    live_env,  # noqa: F401 - the fixture
)
from tests.unit.harness.engines.real_stack import (
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    engine_image,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.engines.test_real_codex_controller import SESSION, TENANT, Lane
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.engines.types import EngineVersion
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(900, method="thread")]

IMAGE = engine_image("claude_code")
RUN_BUDGET_USD = 0.5
RUN_BUDGET_TOKENS = 2_000_000
MAX_USD = 1.0
TASK = (
    "Create the file out/answer.md in the current folder. Its content must be exactly one line: "
    "`17 * 3 = 51`. Use your file-writing tool, then reply with the single word done."
)
_E3_PORT: dict[str, int] = {}


def _pinned() -> EngineVersion:
    return next(r.version for r in release_allowlist() if r.version.kind == "claude_code")


class ClaudeCodeNode(LiveNode):
    """Node's grant for a Claude Code run of the controller lane's tenant."""

    @override
    def grant_run(self, run_id: str, attempt_id: str) -> None:
        assert self.route is not None
        route = self.route
        self.grants[run_id] = (
            "",
            {
                "runId": run_id,
                "attemptId": attempt_id,
                "routesDigest": route_digest((route,)),
                "routes": [
                    {"provider": route.provider, "model": route.model, "modelKey": route.config_key, "modelName": route.config_model}
                ],
                "budgets": {"maxCostUsd": RUN_BUDGET_USD, "maxTokens": RUN_BUDGET_TOKENS},
                "actorId": "user-1",
                "engine": "claude_code",
            },
        )
        self.budget.ledger.pin(RunAccountRef(TENANT, run_id), PinnedBudget(Cap(usd_micros(RUN_BUDGET_USD), RUN_BUDGET_TOKENS)))


@pytest.fixture(scope="module")
def live_stack() -> Iterator[RealStack]:
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/claude-code/build.sh --binary PATH)")

    def upstreams(stack: RealStack) -> dict[str, str]:
        _E3_PORT["port"] = _free_port(stack.host_ip)
        return {"EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{_E3_PORT['port']}"})}

    stack = start_stack(upstreams=upstreams)
    yield stack
    stop_stack(stack)


@pytest.mark.usefixtures("live_env")
async def test_lv1_claude_code_writes_a_file_with_a_live_model_through_the_gateways(
    live_stack: RealStack, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    key = os.environ["TEST_AZURE_OPENAI_API_KEY"]
    route = PinnedRoute("azureopenai", _model(), config_key=MODEL_KEY, config_model=_model())
    async with live_stack.serving() as stack:
        node = ClaudeCodeNode(issued=stack.models.issued, route=route, budget=BudgetNode(InMemoryBudgetLedger(SystemClock()), TENANT))
        node_server = TestServer(node.app(), host="127.0.0.1")
        await node_server.start_server()
        gateway = await create_gateway(
            StackConfig(_ai_models(), await published(stack.models.mint)),  # type: ignore[arg-type]
            node_url=str(node_server.make_url("")).rstrip("/"),
        )
        server = ModelGatewayServer(gateway)
        await server.start(host=stack.host_ip, port=_E3_PORT["port"])
        namespace = f"io.pipeshub.e9live.{uuid.uuid4().hex[:8]}"
        prefix = "pxh-e9l-" + namespace.rsplit(".", 1)[1]
        broker, provider = await engine_broker(stack, namespace, prefix, image=IMAGE)
        try:
            lane = Lane(stack, broker, None, version=_pinned(), image=IMAGE, agent="E9")  # type: ignore[arg-type]
            tools = EndpointConformanceGateway(
                endpoints=EngineControllerConfig(images={}).endpoints("x" * 16, "y" * 16),
                grant=(GrantedTool("search", "Search the knowledge base."),),
                binding=RunBinding(TENANT, SESSION, lane.run_id),
                attempt=1,
                routes_digest=route_digest((route,)),
            )
            lane.gateways[1] = tools
            stack.models.bind(lane.run_id, f"{lane.run_id}.a1", tools.claims.binding)
            node.grant_run(lane.run_id, f"{lane.run_id}.a1")
            run = lane.run(1)
            outcome = await lane.controller.run_turn(run, lane.task(0, TASK))
            head = await lane.head()
        finally:
            await provider.run_blocking(cleanup, provider, namespace, prefix)
            await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
            provider.close()
            await server.stop()
            await node_server.close()
    left = await node.budget.ledger.remaining(RunAccountRef(TENANT, lane.run_id))
    charged = left.committed.usd_micros / 1e6
    usage = [e for _, e in lane.sink.events if isinstance(e, UsageReported)]
    tokens = (sum(u.input_tokens for u in usage), sum(u.output_tokens for u in usage))
    print(
        f"\nLV-1 reason={outcome.reason} final={outcome.final_text.strip()[:40]!r} files={sorted(head)} "
        f"tokens={tokens} usd_e3={charged:.4f} published={[p.path for p in outcome.published]}"
    )
    assert outcome.reason == "completed", (outcome.reason, outcome.limit)
    assert "51" in head["out/answer.md"].decode()
    assert [p.path for p in outcome.published] == ["answer.md"]
    assert 0 < charged < MAX_USD
    assert "model_gateway.refused" not in caplog.text
    assert key not in caplog.text
    assert not any(token in caplog.text for token in stack.models.issued)
