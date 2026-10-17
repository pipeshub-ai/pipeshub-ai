"""`PipesHubAgentFactory.create()` loads an agent's attached API toolset operations
(`OpenAPIToolProvider`, right after the MCP provider): offered to the model, and filed with
the other app integrations under `CONNECTORS_PARENT` when disclosure goes lazy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.lazy_tools_wiring import CONNECTORS_PARENT
from app.agents.openapi import service
from app.agents.openapi.store import ApiToolsetStore
from tests.support.in_memory_config import InMemoryConfigService
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

ACME_SPEC = (Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture" / "openapi.yaml").read_bytes()
INSTANCE_ID = "a" * 32
TOOL = "api_acme_helpdesk_list_tickets"


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Skills wiring would await real graph calls on make_context's MagicMock graph_provider.
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


async def _context(*, enabled: bool = True) -> Any:  # noqa: ANN401
    config = InMemoryConfigService()
    config.values["/services/platform/settings"] = {"featureFlags": {"ENABLE_API_TOOLSETS": enabled}}
    await ApiToolsetStore(config).save_instance(service.build_instance(
        service.InstanceInput(name="Acme Helpdesk", base_url="https://helpdesk.acme.test", operations=["list_tickets"],
                              spec=ACME_SPEC, auth_scheme_name="none"),
        instance_id=INSTANCE_ID, org_id="org-1", user_id="admin-1",
    ))
    return make_context(llm=FakeChatModel(), config_service=config, api_toolset_tools=[f"{INSTANCE_ID}:list_tickets"])


class TestApiToolsetWiring:
    async def test_attached_operation_is_offered_to_the_model(self) -> None:
        context = await _context()
        agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(context, context.llm, "quick", query="hello")
        assert runtime.tool_registry.has(TOOL)
        assert TOOL in agent.spec.tool_names

    async def test_lazy_disclosure_files_it_with_the_app_integrations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
        context = await _context()
        _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(context, context.llm, "quick", query="hello")
        assert TOOL in runtime.tool_registry.tools_in_toolset(CONNECTORS_PARENT)

    async def test_nothing_is_offered_with_the_labs_flag_off(self) -> None:
        context = await _context(enabled=False)
        _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(context, context.llm, "quick", query="hello")
        assert not runtime.tool_registry.has(TOOL)
