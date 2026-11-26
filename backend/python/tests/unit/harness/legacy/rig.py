"""The production chat routes on in-memory stores, for the tests of a legacy turn assembled from its header."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.config.constants.service import config_node_constants
from app.harness.gateway.keys import ConfiguredGatewayKeys
from app.harness.legacy.resolved_turn import (
    RESOLVED_TURN_HEADER,
    ResolvedTurnVerifier,
)
from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_actions_enabled,
    is_skills_enabled,
    is_user_context_enabled,
)
from tests.support import edition_defaults
from tests.support.agent_routes import (
    AGENTS,
    USERS,
    FakeContainer,
    InMemoryGraph,
    _TestAuth,
)
from tests.support.config_service import (
    RIG_PLATFORM_FLAGS,
    FakeConfigurationService,
)
from tests.unit.harness.credentials.support import owned_toolsets
from tests.unit.harness.legacy.support import (
    ACTIVE,
    KEYS_NODE,
    NOW,
    VECTOR,
    claims,
    clock_at,
    keys_document,
    sign,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from httpx import Response

APPROVALS_NODE: str = config_node_constants.AGENT_HARNESS_APPROVALS.value
CONVERSATION = "conv-1"
MCP_INSTANCE = "/services/mcp/instances/mcp-1"
STAGES: dict[str, bool] = {
    **dict.fromkeys(("ENABLE_ACTIONS", "ENABLE_MCP", "ENABLE_SKILLS", "ENABLE_USER_CONTEXT"), True),
    **edition_defaults.worker_stages_extra(),
}


def toolset_path(instance: str, owner: str) -> str:
    return f"/services/toolsets/{instance}/{owner}"


def mcp_credentials_path(owner: str, instance: str = "mcp-1") -> str:
    return f"/services/mcp/credentials/{instance}/{owner}"


def _sse(frames: list[dict[str, Any]]) -> list[str]:
    return [f"event: {f['event']}\ndata: {json.dumps(f['data'])}\n\n" for f in frames]


class RecordingLoop:
    """Stands in for ``run_agent_loop_stream`` / ``run_chat_stream`` at the route's edge: records what the route
    handed it and, inside the stream, what the Labs readers answer there and in a task started there (the loop's
    factory reads them the same way)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reads_before: list[list[str]] = []
        self.flags: list[dict[str, bool]] = []
        self.task_flags: list[dict[str, bool]] = []

    def __call__(self, query_info: dict[str, Any], user_info: dict[str, Any], *args: object, **kwargs: Any) -> AsyncIterator[str]:  # noqa: ANN401
        self.calls.append({"query_info": query_info, "user_info": user_info, "args": args, **kwargs})
        config = kwargs["config_service"] if "config_service" in kwargs else args[5]
        self.reads_before.append(list(config.reads))

        async def read() -> dict[str, bool]:
            return {
                "ENABLE_ACTIONS": await is_actions_enabled(config),
                "ENABLE_SKILLS": await is_skills_enabled(config),
                "ENABLE_USER_CONTEXT": await is_user_context_enabled(config),
            }

        async def events() -> AsyncIterator[str]:
            self.flags.append(await read())
            self.task_flags.append(await asyncio.create_task(read()))
            context = SimpleNamespace(run_id="run-1", conversation_id=CONVERSATION)
            for frame in _sse(AGUI_FORMATTER.answer_final(context, completion_data={"answer": "42", "citations": []})):
                yield frame

        return events()

    @property
    def query_info(self) -> dict[str, Any]:
        (call,) = self.calls
        return call["query_info"]

    @property
    def user_info(self) -> dict[str, Any]:
        (call,) = self.calls
        return call["user_info"]


class World:
    """One org's stores as a legacy turn reads them: an org-shared agent with a toolset the turn holds bound
    (``inst-1``), one it does not (``inst-2``) and an MCP server (``mcp-1``); a service-account agent with ``inst-1``."""

    def __init__(
        self,
        *,
        flags: Mapping[str, bool] | None = None,
        approvals: object = None,
        toolset_owners: tuple[str, ...] = ("u-bob", "u-alice", "sa"),
    ) -> None:
        self.graph = InMemoryGraph()
        self.graph.add_agent("shared", "alice", share_with_org=True, systemPrompt="be brief")
        self.graph.add_agent("private", "alice")
        self.graph.add_agent("sa", "alice", share_with_org=True, isServiceAccount=True)
        for instance in ("inst-1", "inst-2"):
            self.graph.add_node(
                "agentToolsets", {"_key": instance, "name": "jira", "displayName": instance, "instanceId": instance}
            )
            self.graph.add_node("agentTools", {"_key": f"{instance}-t", "name": "search", "fullName": f"{instance}.search"})
            self.graph.add_edge("toolsetHasTool", {"_from": f"agentToolsets/{instance}", "_to": f"agentTools/{instance}-t"})
            self.graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/shared", "_to": f"agentToolsets/{instance}"})
        self.graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/sa", "_to": "agentToolsets/inst-1"})
        self.graph.add_node("agentMcpServers", {"_key": "m1", "instanceId": "mcp-1", "displayName": "Notes", "name": "notes"})
        self.graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/shared", "_to": "agentMcpServers/m1"})
        nodes: dict[str, Any] = owned_toolsets(
            {toolset_path(i, owner): {"isAuthenticated": True} for i in ("inst-1", "inst-2") for owner in toolset_owners}
        )
        nodes[MCP_INSTANCE] = {
            "_id": "mcp-1", "orgId": "org-1", "createdBy": "u-alice", "instanceName": "Notes", "serverType": "notes",
            "transport": "streamable_http", "url": "https://mcp.example.test/mcp", "authMode": "api_token",
        }
        nodes[mcp_credentials_path("u-bob")] = {"isAuthenticated": True}
        nodes[KEYS_NODE] = keys_document(ACTIVE)
        if approvals is not None:
            nodes[APPROVALS_NODE] = approvals
        self.flag_values = {**RIG_PLATFORM_FLAGS, **edition_defaults.worker_stages_extra(), "ENABLE_MCP": True, **(flags or {})}
        self.config = FakeConfigurationService(nodes, flags=self.flag_values)

    def stages(self) -> dict[str, bool]:
        """The Labs stages as Node resolves them from the same settings document."""
        return {name: self.flag_values[name] for name in STAGES}

    def reads_of(self, key: str) -> int:
        return self.config.reads.count(key)

    @property
    def settings_reads(self) -> int:
        return self.reads_of(PLATFORM_SETTINGS_KEY)


def settings_reads(reads: list[str]) -> int:
    return reads.count(PLATFORM_SETTINGS_KEY)


class Rig:
    """The production agent and assistant routers on a ``World``, with the loop and the model replaced at the
    routes' edge. The verifier's clock is the test's."""

    def __init__(self, world: World, monkeypatch: Any, *, now: int = NOW, extra_users: dict[str, dict[str, Any]] | None = None) -> None:  # noqa: ANN401
        from app.api.routes.agent import router as agent_router
        from app.api.routes.chatbot import router as chatbot_router

        self.world = world
        self.now = now
        self.loop = RecordingLoop()
        self.model_calls = 0
        monkeypatch.setattr("app.api.routes.agent.run_agent_loop_stream", self.loop)
        monkeypatch.setattr("app.api.routes.chatbot.run_chat_stream", self._chat_loop)

        async def llm(*_a: object, **_k: object) -> tuple[object, dict[str, Any], dict[str, Any]]:
            self.model_calls += 1
            return object(), {"isMultimodal": False, "provider": "openai"}, {}

        async def no_entity_store(*_a: object, **_k: object) -> None:
            return None

        for module in ("agent", "chatbot"):
            monkeypatch.setattr(f"app.api.routes.{module}.get_llm_for_chat", llm)
            monkeypatch.setattr(f"app.api.routes.{module}.load_entity_vector_store", no_entity_store)
        app = FastAPI()
        app.include_router(agent_router, prefix="/api/v1/agent")
        app.include_router(chatbot_router, prefix="/api/v1")
        self.container = FakeContainer(world.graph, world.config)
        app.container = self.container  # type: ignore[attr-defined]
        app.state.resolved_turn_verifier = ResolvedTurnVerifier(ConfiguredGatewayKeys(world.config), clock_at(now))
        app.add_middleware(_TestAuth, users={**USERS, **(extra_users or {})})
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)

    def _chat_loop(self, query_info: dict[str, Any], user_info: dict[str, Any], *args: object, **kwargs: Any) -> AsyncIterator[str]:  # noqa: ANN401
        return self.loop(query_info, user_info, *args, **kwargs)

    def header(self, caller: str = "bob", *, agent: str | None = "shared", **overrides: Any) -> str:  # noqa: ANN401
        """A header Node would sign for ``caller``'s turn with ``agent`` in the test conversation."""
        user = self.users[caller]
        payload = claims(
            self.now,
            **{
                "org": user["orgId"], "sub": user["userId"], "agent": agent, "cid": CONVERSATION,
                "stages": self.world.stages(), "flow": [], "bindings": None,
                "pin": {**VECTOR["valid"]["payload"]["pin"], "scope": {"agentKey": agent, "projectId": None}},
                **overrides,
            },
        )
        return sign(payload)

    @property
    def users(self) -> dict[str, dict[str, Any]]:
        return self.app.user_middleware[0].kwargs["users"]

    def post(
        self, path: str, caller: str = "bob", *, header: str | None = None, body: dict[str, Any] | None = None
    ) -> Response:
        headers = {"x-test-user": caller, **({RESOLVED_TURN_HEADER: header} if header is not None else {})}
        return self.client.post(path, headers=headers, json={"query": "hi", "conversationId": CONVERSATION, **(body or {})})

    def agent_stream(self, agent: str = "shared", caller: str = "bob", **kwargs: Any) -> Response:  # noqa: ANN401
        return self.post(f"/api/v1/agent/{agent}/chat/stream", caller, **kwargs)

    def reads_before_the_keys(self) -> list[str]:
        return [key for key in self.world.config.reads if key != KEYS_NODE]


def real_now() -> int:
    return int(time.time())
