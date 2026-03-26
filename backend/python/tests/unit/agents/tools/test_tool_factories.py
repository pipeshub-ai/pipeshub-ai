"""Tests for tool factory registry and base factory."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.tools.config import ToolCategory, ToolMetadata
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import Tool, ToolParameter


class TestClientFactoryRegistry:
    """Tests for ClientFactoryRegistry."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry state before/after each test."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry.reset()
        yield
        ClientFactoryRegistry.reset()

    def test_register_and_get_factory(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        mock_factory = MagicMock()
        ClientFactoryRegistry.register("test_app", mock_factory)
        result = ClientFactoryRegistry.get_factory("test_app")
        assert result is mock_factory

    def test_get_factory_not_found(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        # Force initialization to avoid triggering default factories for a missing key
        ClientFactoryRegistry._initialized = True
        result = ClientFactoryRegistry.get_factory("nonexistent_app")
        assert result is None

    def test_unregister_factory(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        mock_factory = MagicMock()
        ClientFactoryRegistry.register("test_app", mock_factory)
        ClientFactoryRegistry.unregister("test_app")
        ClientFactoryRegistry._initialized = True
        result = ClientFactoryRegistry.get_factory("test_app")
        assert result is None

    def test_unregister_nonexistent_is_noop(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        # Should not raise
        ClientFactoryRegistry.unregister("does_not_exist")

    def test_list_factories(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        mock_factory = MagicMock()
        ClientFactoryRegistry._initialized = True
        ClientFactoryRegistry.register("app_a", mock_factory)
        ClientFactoryRegistry.register("app_b", mock_factory)
        names = ClientFactoryRegistry.list_factories()
        assert "app_a" in names
        assert "app_b" in names

    def test_reset_clears_all(self):
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry.register("test_app", MagicMock())
        ClientFactoryRegistry._initialized = True
        ClientFactoryRegistry.reset()
        assert ClientFactoryRegistry._factories == {}
        assert ClientFactoryRegistry._initialized is False

    def test_get_factory_triggers_initialization(self):
        """get_factory should trigger initialize_default_factories if not initialized."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry._initialized = False
        with patch.object(ClientFactoryRegistry, "initialize_default_factories") as mock_init:
            mock_init.side_effect = lambda: setattr(ClientFactoryRegistry, "_initialized", True)
            ClientFactoryRegistry.get_factory("anything")
            mock_init.assert_called_once()

    def test_list_factories_triggers_initialization(self):
        """list_factories should trigger initialize_default_factories if not initialized."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry._initialized = False
        with patch.object(ClientFactoryRegistry, "initialize_default_factories") as mock_init:
            mock_init.side_effect = lambda: setattr(ClientFactoryRegistry, "_initialized", True)
            ClientFactoryRegistry.list_factories()
            mock_init.assert_called_once()

    def test_initialize_default_factories_idempotent(self):
        """Calling initialize_default_factories twice does not duplicate entries."""
        from app.agents.tools.factories.registry import ClientFactoryRegistry
        ClientFactoryRegistry.initialize_default_factories()
        first_count = len(ClientFactoryRegistry._factories)
        ClientFactoryRegistry.initialize_default_factories()
        second_count = len(ClientFactoryRegistry._factories)
        assert first_count == second_count


class TestClientFactoryBase:
    """Tests for the abstract ClientFactory base class."""

    def test_cannot_instantiate_directly(self):
        from app.agents.tools.factories.base import ClientFactory
        with pytest.raises(TypeError):
            ClientFactory()

    def test_create_client_sync_no_event_loop(self):
        """create_client_sync calls asyncio.run when no event loop is running."""
        from app.agents.tools.factories.base import ClientFactory

        class ConcreteFactory(ClientFactory):
            async def create_client(self, config_service, logger, toolset_config, state=None):
                return "test_client"

        factory = ConcreteFactory()
        result = factory.create_client_sync(MagicMock(), MagicMock(), {"key": "val"})
        assert result == "test_client"

    def test_create_client_sync_in_async_context(self):
        """create_client_sync uses thread pool when already in async context."""
        from app.agents.tools.factories.base import ClientFactory

        class ConcreteFactory(ClientFactory):
            async def create_client(self, config_service, logger, toolset_config, state=None):
                return "thread_client"

        factory = ConcreteFactory()

        async def run_test():
            return factory.create_client_sync(MagicMock(), MagicMock(), {"key": "val"})

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_test())
            assert result == "thread_client"
        finally:
            loop.close()


class TestToolRegistry:
    """Tests for the ToolRegistry class."""

    @pytest.fixture
    def registry(self):
        from app.agents.tools.registry import ToolRegistry
        return ToolRegistry()

    @pytest.fixture
    def sample_tool(self):
        return Tool(
            app_name="slack",
            tool_name="send_message",
            description="Send a message to a Slack channel",
            function=lambda x: x,
            parameters=[
                ToolParameter(
                    name="channel",
                    type=ParameterType.STRING,
                    description="Channel name",
                    required=True,
                ),
            ],
        )

    @pytest.fixture
    def sample_metadata(self):
        return ToolMetadata(
            app_name="slack",
            tool_name="send_message",
            description="Send a message to a Slack channel",
            category=ToolCategory.COMMUNICATION,
            is_essential=True,
            requires_auth=True,
            tags=["messaging", "notification"],
        )

    def test_register_tool(self, registry, sample_tool):
        registry.register(sample_tool)
        assert "slack.send_message" in registry.list_tools()

    def test_register_tool_with_metadata(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        meta = registry.get_metadata("slack.send_message")
        assert meta is not None
        assert meta.category == ToolCategory.COMMUNICATION

    def test_register_duplicate_skipped(self, registry, sample_tool):
        registry.register(sample_tool)
        registry.register(sample_tool)
        assert registry.list_tools().count("slack.send_message") == 1

    def test_get_tool(self, registry, sample_tool):
        registry.register(sample_tool)
        tool = registry.get_tool("slack", "send_message")
        assert tool is not None
        assert tool.app_name == "slack"

    def test_get_tool_not_found(self, registry):
        assert registry.get_tool("missing", "tool") is None

    def test_get_tool_by_full_name(self, registry, sample_tool):
        registry.register(sample_tool)
        tool = registry.get_tool_by_full_name("slack.send_message")
        assert tool is not None

    def test_get_tool_by_full_name_not_found(self, registry):
        assert registry.get_tool_by_full_name("missing.tool") is None

    def test_get_tools_by_category(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        tools = registry.get_tools_by_category(ToolCategory.COMMUNICATION)
        assert len(tools) == 1
        assert tools[0].app_name == "slack"

    def test_get_tools_by_category_empty(self, registry):
        tools = registry.get_tools_by_category(ToolCategory.CALENDAR)
        assert tools == []

    def test_get_tools_by_app(self, registry, sample_tool):
        registry.register(sample_tool)
        tools = registry.get_tools_by_app("slack")
        assert len(tools) == 1

    def test_get_tools_by_app_not_found(self, registry):
        tools = registry.get_tools_by_app("missing")
        assert tools == []

    def test_list_tools(self, registry, sample_tool):
        registry.register(sample_tool)
        assert registry.list_tools() == ["slack.send_message"]

    def test_get_all_tools_returns_copy(self, registry, sample_tool):
        registry.register(sample_tool)
        all_tools = registry.get_all_tools()
        assert "slack.send_message" in all_tools
        # Modifying the copy shouldn't affect the registry
        all_tools.clear()
        assert len(registry.list_tools()) == 1

    def test_search_tools_by_query(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        results = registry.search_tools(query="message")
        assert len(results) == 1

    def test_search_tools_by_category(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        results = registry.search_tools(category=ToolCategory.COMMUNICATION)
        assert len(results) == 1

    def test_search_tools_by_tags(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        results = registry.search_tools(tags=["messaging"])
        assert len(results) == 1

    def test_search_tools_by_tags_no_match(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        results = registry.search_tools(tags=["nonexistent"])
        assert len(results) == 0

    def test_search_tools_essential_only(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        results = registry.search_tools(essential_only=True)
        assert len(results) == 1

    def test_search_tools_essential_only_filters_non_essential(self, registry):
        tool = Tool(
            app_name="test",
            tool_name="non_essential",
            description="Not essential",
            function=lambda x: x,
        )
        meta = ToolMetadata(
            app_name="test",
            tool_name="non_essential",
            description="Not essential",
            category=ToolCategory.UTILITY,
            is_essential=False,
        )
        registry.register(tool, meta)
        results = registry.search_tools(essential_only=True)
        assert len(results) == 0

    def test_search_tools_no_query_returns_all(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        results = registry.search_tools()
        assert len(results) == 1

    def test_get_statistics(self, registry, sample_tool, sample_metadata):
        registry.register(sample_tool, sample_metadata)
        stats = registry.get_statistics()
        assert stats["total_tools"] == 1
        assert stats["essential_count"] == 1
        assert stats["requires_auth_count"] == 1
        assert "communication" in stats["by_category"]
        assert "slack" in stats["by_app"]

    def test_generate_openai_schema(self, registry, sample_tool):
        registry.register(sample_tool)
        schemas = registry.generate_openai_schema()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "slack.send_message"
        assert "channel" in schema["function"]["parameters"]["properties"]
        assert "channel" in schema["function"]["parameters"]["required"]

    def test_generate_anthropic_schema(self, registry, sample_tool):
        registry.register(sample_tool)
        schemas = registry.generate_anthropic_schema()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["name"] == "slack.send_message"
        assert "channel" in schema["input_schema"]["properties"]
        assert "channel" in schema["input_schema"]["required"]


class TestToolModel:
    """Tests for the Tool dataclass."""

    def test_tool_name_property(self):
        tool = Tool(
            app_name="jira",
            tool_name="create_issue",
            description="Create a Jira issue",
            function=lambda x: x,
        )
        assert tool.name == "jira.create_issue"

    def test_tool_default_values(self):
        tool = Tool(
            app_name="test",
            tool_name="t",
            description="d",
            function=lambda x: x,
        )
        assert tool.parameters == []
        assert tool.examples == []
        assert tool.tags == []
        assert tool.when_to_use == []
        assert tool.when_not_to_use == []
        assert tool.typical_queries == []
        assert tool.args_schema is None
        assert tool.llm_description is None
        assert tool.returns is None


class TestToolParameter:
    """Tests for the ToolParameter dataclass."""

    def test_to_dict(self):
        param = ToolParameter(
            name="channel",
            type=ParameterType.STRING,
            description="Channel name",
            required=True,
            enum=["general", "random"],
        )
        d = param.to_dict()
        assert d["name"] == "channel"
        assert d["type"] == "string"
        assert d["required"] is True
        assert d["enum"] == ["general", "random"]

    def test_to_json_serializable_dict(self):
        param = ToolParameter(
            name="count",
            type=ParameterType.INTEGER,
            description="Number of items",
            required=False,
            default=10,
        )
        d = param.to_json_serializable_dict()
        assert d["name"] == "count"
        assert d["type"] == "integer"
        assert d["default"] == 10

    def test_from_dict(self):
        data = {
            "name": "query",
            "type": "string",
            "description": "Search query",
            "required": True,
        }
        param = ToolParameter.from_dict(data)
        assert param.name == "query"
        assert param.description == "Search query"
        assert param.required is True

    def test_from_dict_defaults(self):
        param = ToolParameter.from_dict({})
        assert param.name == ""
        assert param.required is True
        assert param.default is None
        assert param.enum is None
