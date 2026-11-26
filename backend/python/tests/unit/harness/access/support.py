"""The model gateway as ``app.model_gateway_main`` wires it, over fakes of what it reads.

Configuration (``aiModels``, Node's published gateway keys, the worker credential, no Redis) is a
fake ``ConfigurationService``; Node is a small aiohttp app answering the
model-grant and budget-ledger routes for the tokens it was told about (the ledger
is the in-memory reference) and each org's live layer, which the gateway reads with the
control plane's pin on (the default, C4.04); the gateway listens on a free port of 127.0.0.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from app.config.constants.service import config_node_constants
from app.model_gateway_main import create_gateway
from harness_core.access.types import PinnedRoute, route_digest
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.tree import usd_micros
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.clock import SystemClock
from harness_core.gateway.model_server import ModelGatewayServer
from harness_core.gateway.types import RunAttemptBinding
from harness_core.testing.clock import ManualClock
from harness_core.testing.gateway import TokenMint
from tests.unit.harness.access.budget_node import BudgetNode


@dataclass
class StackConfig:
    ai_models: dict[str, Any]
    public_keys: dict[str, Any]
    # ``/services/agentHarness/modelAccess`` as Node writes it (A1); ``None``: no document.
    model_access: dict[str, Any] | None = None
    worker_credential: str = "worker-credential-not-real"

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
        if key == config_node_constants.AI_MODELS.value:
            return self.ai_models
        if key == config_node_constants.AGENT_HARNESS_GATEWAY_PUBLIC_KEYS.value:
            return self.public_keys
        if key == config_node_constants.AGENT_HARNESS_MODEL_ACCESS.value:
            return self.model_access
        if key == config_node_constants.AGENT_HARNESS_WORKER_CREDENTIAL.value:
            return {"current": self.worker_credential}
        return default

    async def get_redis_config(self) -> dict[str, Any]:
        raise RuntimeError("no Redis in this stack")


@dataclass
class FakeNode:
    grants: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    asked: int = 0
    # The org's live layer (CPD-6) as the gateway reads it under the pin; empty narrows nothing.
    live_layer: dict[str, Any] = field(default_factory=lambda: {"schemaVersion": 1, "scope": {"kind": "live"}})
    budget: BudgetNode = field(default_factory=lambda: BudgetNode(InMemoryBudgetLedger(SystemClock()), "org-1"))

    def authorized(self, request: web.Request) -> bool:
        token, _ = self.grants.get(request.match_info["runId"], ("", {}))
        return bool(token) and request.headers.get("Authorization") == f"Bearer {token}"

    async def ledger(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": {"code": "AGENT_HARNESS_ATTEMPT_CLAIMS"}}, status=401)
        run_id = request.match_info["runId"]
        rest = request.path.split(f"/runs/{run_id}/", 1)[1].split("/")
        body = await request.json() if request.can_read_body else {}
        status, answer = await self.budget.answer(request.method, run_id, rest, body)
        return web.json_response(answer, status=status)

    async def model_grant(self, request: web.Request) -> web.Response:
        self.asked += 1
        run_id = request.match_info["runId"]
        token, grant = self.grants.get(run_id, ("", {}))
        if not token or request.headers.get("Authorization") != f"Bearer {token}":
            return web.json_response({"error": {"code": "AGENT_HARNESS_ATTEMPT_CLAIMS"}}, status=401)
        return web.json_response(grant, headers={"Cache-Control": "no-store"})

    async def live(self, request: web.Request) -> web.Response:
        body = {
            "schemaVersion": 1,
            "orgId": request.match_info["orgId"],
            "revision": 1,
            "layers": [{"layer": self.live_layer}],
            "revokedBindings": [],
        }
        return web.json_response(body)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v1/agent-harness/internal/control-plane/orgs/{orgId}/live", self.live)
        runs = "/api/v1/agent-harness/internal/runs/{runId}"
        app.router.add_get(f"{runs}/model-grant", self.model_grant)
        app.router.add_get(f"{runs}/budget", self.ledger)
        app.router.add_post(f"{runs}/budget/reservations", self.ledger)
        app.router.add_post(f"{runs}/budget/reservations/{{key}}/{{action}}", self.ledger)
        return app


@dataclass
class Stack:
    url: str
    token: str
    binding: RunAttemptBinding
    node: FakeNode
    servers: list[Any]
    config: StackConfig

    async def close(self) -> None:
        for server in reversed(self.servers):
            await (server.stop() if isinstance(server, ModelGatewayServer) else server.close())


async def published(mint: TokenMint) -> dict[str, Any]:
    """The mint's keys as Node writes ``/services/agentHarness/gatewayPublicKeys``."""
    keys = await mint.source.keys()
    assert keys is not None
    return {
        "issuer": keys.issuer,
        "audience": keys.audience,
        "keys": [
            {"kid": k.kid, "alg": "EdDSA", "publicKey": k.public_key_pem, "retiredAt": k.retired_at_ms}
            for k in keys.keys
        ],
    }


async def start_stack(
    ai_models: dict[str, Any],
    route: PinnedRoute,
    *,
    max_cost_usd: float = 1.0,
    max_tokens: int = 100_000,
    actor: str = "user-1",
    engine: str = "opencode",
) -> Stack:
    import datetime as dt

    mint = TokenMint(ManualClock(dt.datetime.now(dt.UTC)))
    binding = RunAttemptBinding(
        tenant="org-1",
        scope="session-1",
        run_id="run-1",
        parent_run_id=None,
        attempt_id="run-1-a1",
        fence=1,
        tool_ids_digest="tools",
        routes_digest=route_digest((route,)),
    )
    token = mint.mint(binding)
    node = FakeNode()
    node.budget.ledger.pin(
        RunAccountRef("org-1", "run-1"), PinnedBudget(Cap(usd_micros(max_cost_usd), max_tokens))
    )
    node.grants["run-1"] = (
        token,
        {
            "runId": "run-1",
            "attemptId": "run-1-a1",
            "routesDigest": binding.routes_digest,
            "routes": [
                {
                    "provider": route.provider,
                    "model": route.model,
                    "modelKey": route.config_key,
                    "modelName": route.config_model,
                    **(
                        {"account": {"kind": route.account.kind.value, "accountId": route.account.account_id}}
                        if route.account is not None
                        else {}
                    ),
                }
            ],
            "budgets": {"maxCostUsd": max_cost_usd, "maxTokens": max_tokens},
            "actorId": actor,
            "engine": engine,
        },
    )
    node_server = TestServer(node.app(), host="127.0.0.1")
    await node_server.start_server()
    config = StackConfig(ai_models, await published(mint))
    gateway = await create_gateway(config, node_url=str(node_server.make_url("")).rstrip("/"))  # type: ignore[arg-type]
    server = ModelGatewayServer(gateway)
    port = await server.start(host="127.0.0.1", port=0)
    return Stack(f"http://127.0.0.1:{port}", token, binding, node, [node_server, server], config)
