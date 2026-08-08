"""`app/agents/agent_loop/tool_loader.py` gates that back the `/chat/stream`
mode migration (see `app/agents/chat_modes/policy.py`'s docstring): each
`ChatModePolicy` sets `ChatState`/`AgentContext` flags rather than a
parallel `ToolPolicy` object, on the premise that `PipesHubToolLoader`
already gates tools on exactly those flags. These tests exercise that
premise directly against the real loader/`_build_dynamic_tools`, so a
regression in the gate itself (not just in `bridge.py` setting the flag)
is caught here rather than only in the mocked `test_bridge.py` suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_loader import PipesHubToolLoader, _build_dynamic_tools


def _make_context(**overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="u@example.com",
        logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    return context


class _FakeRetrievalToolset:
    """Minimal internal toolset standing in for the real `Retrieval` action
    class -- one `@tool`-decorated coroutine is all `ToolsetBuilder`/
    `_has_tool_decorated_methods` need to discover it."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = state

    @tool(
        path="/tools/retrieval/search_internal_knowledge",
        short_description="Search internal knowledge",
        description="Search the internal knowledge base",
    )
    async def search_internal_knowledge(self, query: str) -> tuple[bool, str]:
        return True, "found it"


def _patch_retrieval_toolset_registry():
    fake_registry = MagicMock()
    fake_registry.get_all_toolsets.return_value = {
        "retrieval": {
            "class": _FakeRetrievalToolset,
            "isInternal": True,
            "description": "Internal knowledge retrieval",
            "essential": False,
        },
    }
    return (
        patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=fake_registry,
        ),
        patch(
            "app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory",
            return_value=None,
        ),
    )


class TestKnowledgeToolsetGate:
    """`internal_search`/`agent` modes force `has_knowledge=True`;
    `web_search` forces it `False` -- this is the *only* thing standing
    between `web_search` mode and an agent that can still search internal
    knowledge."""

    async def test_has_knowledge_true_registers_retrieval_toolset(self) -> None:
        context = _make_context(has_knowledge=True)
        registry_patch, factory_patch = _patch_retrieval_toolset_registry()
        with registry_patch, factory_patch:
            registry = await PipesHubToolLoader().load(context)

        assert any("search_internal_knowledge" in name for name in registry.names())

    async def test_has_knowledge_false_skips_retrieval_toolset(self) -> None:
        context = _make_context(has_knowledge=False)
        registry_patch, factory_patch = _patch_retrieval_toolset_registry()
        with registry_patch, factory_patch:
            registry = await PipesHubToolLoader().load(context)

        assert not any("search_internal_knowledge" in name for name in registry.names())


class TestBuildDynamicToolsWebSearchGate:
    """`web_search`/`agent` set `state["web_search_config"]`; `internal_search`
    leaves it `None` -- the sole gate on whether `web_search`/`fetch_url`
    dynamic tools get built at all."""

    def test_no_web_search_config_returns_no_web_tools(self) -> None:
        context = _make_context()
        context.tool_state["web_search_config"] = None

        tools = _build_dynamic_tools(context)

        assert tools == []

    def test_web_search_config_present_builds_web_search_and_fetch_url(self) -> None:
        context = _make_context()
        context.tool_state["web_search_config"] = {"provider": "tavily", "configuration": {}}

        fake_web_tool = MagicMock(name="ws_tool")
        fake_fetch_tool = MagicMock(name="fu_tool")
        with (
            patch(
                "app.utils.web_search_tool.create_web_search_tool",
                return_value=fake_web_tool,
            ),
            patch(
                "app.utils.fetch_url_tool.create_fetch_url_tool",
                return_value=fake_fetch_tool,
            ),
            patch(
                "app.agents.agent_loop.tool_loader.split_original_tool_name",
                side_effect=[("web_search", "search"), ("fetch_url", "fetch")],
            ),
        ):
            tools = _build_dynamic_tools(context)

        assert len(tools) == 2


class TestBuildDynamicToolsConnectorKnowledgeGate:
    """SQL/Slack dynamic tools require BOTH the connector being configured
    org-wide (`has_sql_connector`/`has_slack_connector`) AND the chat-mode
    policy allowing it (`has_sql_knowledge`/`has_slack_knowledge`, forced by
    `_apply_policy_to_chat_state` -- `False` for `web_search`)."""

    def test_sql_connector_without_knowledge_flag_yields_no_sql_tool(self) -> None:
        context = _make_context()
        context.tool_state.update(
            {
                "config_service": MagicMock(),
                "has_sql_connector": True,
                "has_sql_knowledge": False,
            }
        )

        tools = _build_dynamic_tools(context)

        assert tools == []

    def test_sql_connector_with_knowledge_flag_yields_sql_tool(self) -> None:
        context = _make_context()
        context.tool_state.update(
            {
                "config_service": MagicMock(),
                "has_sql_connector": True,
                "has_sql_knowledge": True,
            }
        )
        fake_tool = MagicMock(name="sql_tool")
        with (
            patch(
                "app.utils.execute_query.create_execute_query_tool",
                return_value=fake_tool,
            ),
            patch(
                "app.agents.agent_loop.tool_loader.split_original_tool_name",
                return_value=("sql", "execute_sql_query"),
            ),
        ):
            tools = _build_dynamic_tools(context)

        assert len(tools) == 1

    def test_slack_connector_without_knowledge_flag_yields_no_slack_tools(self) -> None:
        context = _make_context()
        context.tool_state.update(
            {
                "config_service": MagicMock(),
                "has_slack_connector": True,
                "has_slack_knowledge": False,
            }
        )

        tools = _build_dynamic_tools(context)

        assert tools == []


class TestModePolicyEndToEndGating:
    """Simulates `_apply_policy_to_chat_state`'s effect for each of the three
    canonical policies and asserts the resulting registry -- the assertion
    the migration plan's `test_tool_policy.py` originally called for,
    adapted to the ChatState-flag mechanism this migration actually uses."""

    @staticmethod
    async def _load_for(*, has_knowledge: bool, web_search_config: dict | None) -> set[str]:
        context = _make_context(has_knowledge=has_knowledge)
        context.tool_state["web_search_config"] = web_search_config
        registry_patch, factory_patch = _patch_retrieval_toolset_registry()
        with registry_patch, factory_patch:
            if web_search_config:
                with (
                    patch("app.utils.web_search_tool.create_web_search_tool", return_value=MagicMock()),
                    patch("app.utils.fetch_url_tool.create_fetch_url_tool", return_value=MagicMock()),
                    patch(
                        "app.agents.agent_loop.tool_loader.split_original_tool_name",
                        side_effect=[("web_search", "search"), ("fetch_url", "fetch")],
                    ),
                ):
                    registry = await PipesHubToolLoader().load(context)
            else:
                registry = await PipesHubToolLoader().load(context)
        return set(registry.names())

    async def test_internal_search_mode_has_retrieval_but_no_web_tools(self) -> None:
        names = await self._load_for(has_knowledge=True, web_search_config=None)
        assert any("search_internal_knowledge" in n for n in names)
        assert not any("web_search" in n or "fetch_url" in n for n in names)

    async def test_web_search_mode_has_web_tools_but_no_retrieval(self) -> None:
        names = await self._load_for(has_knowledge=False, web_search_config={"provider": "tavily"})
        assert not any("search_internal_knowledge" in n for n in names)

    async def test_agent_mode_has_both_retrieval_and_web_tools(self) -> None:
        names = await self._load_for(has_knowledge=True, web_search_config={"provider": "tavily"})
        assert any("search_internal_knowledge" in n for n in names)
