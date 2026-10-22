"""Least privilege at create time and the audit line."""

import json
import logging
from typing import Any

import pytest

from app.agents.constants.toolset_constants import get_toolset_config_path
from app.api.routes.toolset_resolvers import DEFAULT_TOOLSET_INSTANCES_PATH
from app.modules.agents.service.agent_service import AgentService
from app.modules.agents.service.errors import AccessViolationError
from app.modules.agents.service.models import (
    AgentActor,
    AgentSpec,
    ChatProvenance,
)
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    user_key,
)

ALICE = AgentActor(user_key=user_key("alice"), user_id="u-alice", org_id="org-1")
PROVENANCE = ChatProvenance(conversation_id="c-1", message_id="m-1")


class AccessGraph(InMemoryGraph):
    """Alice reaches connector `c-ok` and holds a role on collection `kb-ok` only."""

    def __init__(self, *, fallback: str | None = None) -> None:
        super().__init__()
        self.fallback = fallback

    async def get_accessible_containers(self, user_id: str, org_id: str, **_: object) -> AccessibleContainers:
        self._enter("get_accessible_containers", user_id, org_id)
        if self.fallback:
            return AccessibleContainers(fallback_reason=self.fallback)
        return AccessibleContainers(app_ids=frozenset({"c-ok"}))

    async def get_user_kb_permission(self, kb_id: str, user_id: str, transaction: str | None = None) -> str | None:
        self._enter("get_user_kb_permission", kb_id, user_id)
        return "READER" if kb_id == "kb-ok" else None


def _config(*, authenticated: bool = True, missing: bool = False) -> FakeConfigService:
    values: dict[str, Any] = {
        DEFAULT_TOOLSET_INSTANCES_PATH: [{"_id": "inst-1", "orgId": "org-1"}, {"_id": "inst-other", "orgId": "org-2"}],
    }
    if not missing:
        values[get_toolset_config_path("inst-1", "u-alice")] = {"isAuthenticated": authenticated}
    return FakeConfigService(values)


def _service(graph: InMemoryGraph, config: FakeConfigService | None = None) -> AgentService:
    return AgentService(graph, config or _config(), logging.getLogger("test.access"))  # type: ignore[arg-type]


def _toolset(instance: str = "inst-1") -> dict[str, Any]:
    return {"name": "jira", "instanceId": instance, "tools": [{"name": "search"}]}


def _code(exc: pytest.ExceptionInfo[AccessViolationError]) -> dict[str, Any]:
    assert exc.value.status_code == 400
    assert isinstance(exc.value.detail, dict)
    return exc.value.detail


class TestChatOriginIsEnforced:
    @pytest.mark.asyncio
    async def test_collection_without_a_role_is_refused_before_any_write(self) -> None:
        graph = AccessGraph()
        spec = AgentSpec(name="A", knowledge=[{"connectorId": "kb-secret"}])

        with pytest.raises(AccessViolationError) as exc:
            await _service(graph).create(ALICE, spec, origin="chat", provenance=PROVENANCE)

        assert _code(exc) == {
            "code": "INVALID_KNOWLEDGE", "message": "Some knowledge sources are not available to you.", "ids": ["kb-secret"],
        }
        assert graph.calls_to("begin_transaction") == []
        assert graph.nodes.get(AGENTS, {}) == {}

    @pytest.mark.asyncio
    async def test_connector_the_user_cannot_reach_is_refused(self) -> None:
        spec = AgentSpec(name="A", knowledge=[{"connectorId": "c-ok"}, {"connectorId": "c-nope"}])

        with pytest.raises(AccessViolationError) as exc:
            await _service(AccessGraph()).create(ALICE, spec, origin="chat", provenance=PROVENANCE)

        assert _code(exc)["ids"] == ["c-nope"]

    @pytest.mark.asyncio
    async def test_reachable_connector_and_collection_with_a_role_pass(self) -> None:
        graph = AccessGraph()
        spec = AgentSpec(name="A", knowledge=[{"connectorId": "c-ok"}, {"connectorId": "kb-ok"}])

        created = await _service(graph).create(ALICE, spec, origin="chat", provenance=PROVENANCE)

        assert len(graph.committed) == 1 and created.agent_key in graph.nodes[AGENTS]

    @pytest.mark.asyncio
    async def test_knowledge_that_cannot_be_verified_is_refused(self) -> None:
        graph = AccessGraph(fallback="membership_not_backfilled:x")
        spec = AgentSpec(name="A", knowledge=[{"connectorId": "c-ok"}])

        with pytest.raises(AccessViolationError) as exc:
            await _service(graph).create(ALICE, spec, origin="chat", provenance=PROVENANCE)

        assert _code(exc)["code"] == "INVALID_KNOWLEDGE"

    @pytest.mark.asyncio
    async def test_provider_error_fails_closed(self) -> None:
        graph = AccessGraph()
        graph.fail("get_accessible_containers")

        with pytest.raises(AccessViolationError) as exc:
            await _service(graph).create(ALICE, AgentSpec(name="A", knowledge=[{"connectorId": "c-ok"}]), origin="chat")

        assert _code(exc)["ids"] == ["c-ok"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config", "instance"),
        [
            (_config(authenticated=False), "inst-1"),
            (_config(missing=True), "inst-1"),
            (_config(), "inst-ghost"),
            (_config(), "inst-other"),
        ],
        ids=["not-authenticated", "never-signed-in", "unknown-instance", "other-org-instance"],
    )
    async def test_toolset_must_be_configured_and_authenticated(self, config: FakeConfigService, instance: str) -> None:
        graph = AccessGraph()
        spec = AgentSpec(name="A", toolsets=[_toolset(instance)])

        with pytest.raises(AccessViolationError) as exc:
            await _service(graph, config).create(ALICE, spec, origin="chat", provenance=PROVENANCE)

        assert _code(exc)["code"] == "INVALID_TOOLSET" and _code(exc)["ids"] == [instance]
        assert graph.calls_to("begin_transaction") == []

    @pytest.mark.asyncio
    async def test_toolset_without_an_instance_is_refused(self) -> None:
        with pytest.raises(AccessViolationError) as exc:
            await _service(AccessGraph()).create(
                ALICE, AgentSpec(name="A", toolsets=[{"name": "jira", "tools": [{"name": "a"}]}]), origin="chat",
            )

        assert _code(exc)["ids"] == ["jira"]

    @pytest.mark.asyncio
    async def test_authenticated_toolset_is_created(self) -> None:
        graph = AccessGraph()

        created = await _service(graph).create(ALICE, AgentSpec(name="A", toolsets=[_toolset()]), origin="chat")

        assert [t["name"] for t in created.agent["toolsets"]] == ["jira"]

    @pytest.mark.asyncio
    async def test_mcp_servers_are_not_attachable_from_a_chat(self) -> None:
        spec = AgentSpec(name="A", mcpServers=[{"instanceId": "mi-1", "name": "github"}])

        with pytest.raises(AccessViolationError) as exc:
            await _service(AccessGraph()).create(ALICE, spec, origin="chat")

        assert _code(exc)["code"] == "INVALID_TOOLSET" and _code(exc)["ids"] == ["mi-1"]

    @pytest.mark.asyncio
    async def test_service_account_is_refused(self) -> None:
        graph = AccessGraph()

        with pytest.raises(AccessViolationError) as exc:
            await _service(graph).create(ALICE, AgentSpec(name="A", isServiceAccount=True), origin="chat")

        assert _code(exc)["code"] == "SERVICE_ACCOUNT_NOT_ALLOWED"
        assert graph.nodes.get(AGENTS, {}) == {}

    @pytest.mark.asyncio
    async def test_share_with_org_is_forced_off(self) -> None:
        graph = AccessGraph()

        created = await _service(graph).create(ALICE, AgentSpec(name="A", shareWithOrg=True), origin="chat", provenance=PROVENANCE)

        edges = graph.edges["permission"]
        assert [(e["role"], e["type"]) for e in edges] == [("OWNER", "USER")]
        assert created.agent_key in graph.nodes[AGENTS]


class TestBuilderOriginIsNotChecked:
    @pytest.mark.asyncio
    async def test_a_builder_create_is_written_as_asked(self) -> None:
        graph = AccessGraph()
        spec = AgentSpec(name="Bot", isServiceAccount=True, shareWithOrg=True, knowledge=[{"connectorId": "kb-secret"}])

        created = await _service(graph).create(ALICE, spec)

        assert graph.nodes[AGENTS][created.agent_key]["isServiceAccount"] is True
        assert {e["type"] for e in graph.edges["permission"]} == {"USER", "ORG"}
        assert graph.calls_to("get_accessible_containers") == []


class TestAudit:
    @pytest.mark.asyncio
    async def test_ui_create_is_audited_without_provenance(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="test.access"):
            await _service(AccessGraph()).create(ALICE, AgentSpec(name="Plain"))

        audit = json.loads(next(r.getMessage() for r in caplog.records if r.getMessage().startswith("agent.audit")).split(" ", 1)[1])
        assert (audit["createdVia"], audit["sourceConversationId"]) == ("ui", None)
