"""Unit tests for agent.py MCP server integration.

Tests the MCP-specific functions in agent.py:
- _parse_mcp_servers: parsing mcpServers from request body
- _create_mcp_server_edges: creating graph vertices/edges
- build_initial_state: MCP fields populated in ChatState
- _load_mcp_tools: tool_system loading MCP tool wrappers
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Pre-import stubs.
#
# agent.py and tool_system.py have deep transitive import chains that pull
# in dozens of packages (langgraph, sentence_transformers, aiohttp, jinja2,
# google.oauth2, etc.) many of which aren't installed in the test venv.
#
# Strategy: stub the heavy *application* modules (reranker, retrieval,
# factories, etc.) as MagicMocks so the import chain stops early.
# We leave langchain_core real (it IS installed) so that MCPToolWrapper
# inherits from the real BaseTool.
# ---------------------------------------------------------------------------


def _install_mock_module(name: str) -> MagicMock:
    """Install a MagicMock as *name* in sys.modules."""
    mock = MagicMock()
    mock.__path__ = []
    mock.__name__ = name
    mock.__package__ = name.rsplit(".", 1)[0] if "." in name else name
    mock.__spec__ = importlib.machinery.ModuleSpec(name, None)
    sys.modules[name] = mock
    return mock


# Fix langchain_core if a prior test file (e.g. test_wrapper.py) installed a
# bare ModuleType stub without __path__, breaking submodule imports.
_lc = sys.modules.get("langchain_core")
if _lc is not None and not getattr(_lc, "__path__", None):
    # Remove the broken stub and all its submodules so the real package can load
    _to_remove = [k for k in sys.modules if k == "langchain_core" or k.startswith("langchain_core.")]
    for _k in _to_remove:
        del sys.modules[_k]

# Third-party packages NOT installed in the test venv.
# MagicMock allows arbitrary attribute access.
for _pkg in [
    "langgraph", "langgraph.graph", "langgraph.graph.state",
    "sentence_transformers",
    "opik", "opik.integrations", "opik.integrations.langchain",
]:
    if _pkg not in sys.modules:
        _install_mock_module(_pkg)

# Heavy app modules that agent.py / chat_state.py / tool_system.py pull in.
# Stubbing these breaks the deep import chains early.
_HEAVY_APP_STUBS = [
    # agent.py direct imports
    "app.api.routes.chatbot",
    "app.modules.agents.qna.graph",
    "app.modules.agents.qna.cache_manager",
    "app.modules.agents.qna.memory_optimizer",
    "app.modules.agents.deep.graph",
    "app.modules.agents.deep.state",
    "app.modules.agents.capability_summary",
    # chat_state.py imports (pulls in retrieval, reranker etc.)
    "app.modules.reranker",
    "app.modules.reranker.reranker",
    "app.modules.retrieval",
    "app.modules.retrieval.retrieval_service",
    "app.utils.chat_helpers",
    # tool_system.py -> factories chain (pulls in google, mariadb, aiohttp etc.)
    "app.agents.tools",
    "app.agents.tools.wrapper",
    "app.agents.tools.registry",
    "app.agents.tools.factories",
    "app.agents.tools.factories.registry",
    "app.agents.tools.factories.base",
    "app.agents.tools.factories.google",
    "app.agents.tools.factories.mariadb",
    "app.agents.tools.models",
]

for _mod in _HEAVY_APP_STUBS:
    if _mod not in sys.modules:
        _install_mock_module(_mod)


# ============================================================================
# _parse_mcp_servers
# ============================================================================


class TestParseMcpServers:
    """Tests for _parse_mcp_servers helper."""

    def test_empty_input(self):
        from app.api.routes.agent import _parse_mcp_servers

        assert _parse_mcp_servers([]) == {}
        assert _parse_mcp_servers(None) == {}
        assert _parse_mcp_servers("not a list") == {}

    def test_single_server_no_tools(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [{"name": "GitHub", "type": "github"}]
        result = _parse_mcp_servers(raw)
        assert "github" in result
        assert result["github"]["type"] == "github"
        assert result["github"]["tools"] == []

    def test_single_server_with_tools(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [
            {
                "name": "GitHub",
                "type": "github",
                "instanceId": "inst-1",
                "tools": [
                    {"name": "create_issue", "namespacedName": "mcp_github_create_issue", "description": "Create issue"},
                    {"name": "list_repos", "description": "List repos"},
                ],
            }
        ]
        result = _parse_mcp_servers(raw)
        assert len(result["github"]["tools"]) == 2
        assert result["github"]["instanceId"] == "inst-1"
        assert result["github"]["tools"][0]["namespacedName"] == "mcp_github_create_issue"
        assert result["github"]["tools"][1]["namespacedName"] == "mcp_github_list_repos"

    def test_null_input_schema_defaults_to_object_schema(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [
            {
                "name": "jira",
                "tools": [
                    {
                        "name": "create_issue",
                        "namespacedName": "mcp_jira_create_issue",
                        "inputSchema": None,
                    }
                ],
            }
        ]
        result = _parse_mcp_servers(raw)
        parsed_tool = result["jira"]["tools"][0]
        assert parsed_tool["inputSchema"] == {"type": "object", "properties": {}}

    def test_skips_non_dict_entries(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [None, "bad", 123, {"name": "Valid", "type": "custom"}]
        result = _parse_mcp_servers(raw)
        assert len(result) == 1
        assert "valid" in result

    def test_skips_entries_without_name(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [{"type": "github"}, {"name": "", "type": "slack"}]
        result = _parse_mcp_servers(raw)
        assert len(result) == 0

    def test_duplicate_server_name_merges(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [
            {"name": "github", "type": "github", "tools": [{"name": "tool_a"}]},
            {"name": "github", "type": "github", "instanceId": "inst-2", "tools": [{"name": "tool_b"}]},
        ]
        result = _parse_mcp_servers(raw)
        assert len(result) == 1
        assert len(result["github"]["tools"]) == 2
        assert result["github"]["instanceId"] == "inst-2"

    def test_display_name_defaults(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [{"name": "my_server", "type": "custom"}]
        result = _parse_mcp_servers(raw)
        assert result["my_server"]["displayName"] == "My Server"

    def test_display_name_preserved(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [{"name": "gh", "displayName": "GitHub Enterprise", "type": "github"}]
        result = _parse_mcp_servers(raw)
        assert result["gh"]["displayName"] == "GitHub Enterprise"

    def test_skips_tools_without_name(self):
        from app.api.routes.agent import _parse_mcp_servers

        raw = [
            {
                "name": "slack",
                "type": "slack",
                "tools": [{"name": ""}, {"description": "no name"}, {"name": "send_message"}],
            }
        ]
        result = _parse_mcp_servers(raw)
        assert len(result["slack"]["tools"]) == 1
        assert result["slack"]["tools"][0]["name"] == "send_message"


# ============================================================================
# _create_mcp_server_edges
# ============================================================================


class TestCreateMcpServerEdges:
    """Tests for _create_mcp_server_edges graph operations."""

    @pytest.fixture
    def mock_graph_provider(self):
        gp = AsyncMock(spec=["batch_upsert_nodes", "batch_create_edges"])
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(return_value=True)
        return gp

    @pytest.fixture
    def user_info(self):
        return {"userId": "user-1", "orgId": "org-1"}

    @pytest.fixture
    def mock_logger(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_empty_mcp_servers(self, mock_graph_provider, user_info, mock_logger):
        from app.api.routes.agent import _create_mcp_server_edges

        created, failed = await _create_mcp_server_edges(
            "agent-1", {}, user_info, "user-key-1", mock_graph_provider, mock_logger
        )
        assert created == []
        assert failed == []
        mock_graph_provider.batch_upsert_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_server_nodes_and_edges(self, mock_graph_provider, user_info, mock_logger):
        from app.api.routes.agent import _create_mcp_server_edges

        mcp_servers = {
            "github": {
                "displayName": "GitHub",
                "type": "github",
                "instanceId": "inst-1",
                "instanceName": "My GitHub",
                "tools": [
                    {"name": "create_issue", "namespacedName": "mcp_github_create_issue", "description": "Create issue"},
                ],
            }
        }
        created, failed = await _create_mcp_server_edges(
            "agent-1", mcp_servers, user_info, "user-key-1", mock_graph_provider, mock_logger
        )
        assert len(created) == 1
        assert failed == []
        assert mock_graph_provider.batch_upsert_nodes.call_count == 2
        assert mock_graph_provider.batch_create_edges.call_count == 2

    @pytest.mark.asyncio
    async def test_server_node_batch_failure(self, mock_graph_provider, user_info, mock_logger):
        from app.api.routes.agent import _create_mcp_server_edges

        mock_graph_provider.batch_upsert_nodes = AsyncMock(return_value=None)
        mcp_servers = {
            "github": {
                "displayName": "GitHub",
                "type": "github",
                "tools": [],
            }
        }
        created, failed = await _create_mcp_server_edges(
            "agent-1", mcp_servers, user_info, "user-key-1", mock_graph_provider, mock_logger
        )
        assert len(failed) == 1
        assert failed[0]["name"] == "all"

    @pytest.mark.asyncio
    async def test_server_node_exception(self, mock_graph_provider, user_info, mock_logger):
        from app.api.routes.agent import _create_mcp_server_edges

        mock_graph_provider.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("DB error"))
        mcp_servers = {
            "github": {
                "displayName": "GitHub",
                "type": "github",
                "tools": [],
            }
        }
        created, failed = await _create_mcp_server_edges(
            "agent-1", mcp_servers, user_info, "user-key-1", mock_graph_provider, mock_logger
        )
        assert len(failed) == 1
        assert "DB error" in failed[0]["error"]

    @pytest.mark.asyncio
    async def test_multiple_servers_with_tools(self, mock_graph_provider, user_info, mock_logger):
        from app.api.routes.agent import _create_mcp_server_edges

        mcp_servers = {
            "github": {
                "displayName": "GitHub",
                "type": "github",
                "tools": [
                    {"name": "create_issue", "namespacedName": "mcp_github_create_issue"},
                ],
            },
            "slack": {
                "displayName": "Slack",
                "type": "slack",
                "tools": [
                    {"name": "send_message", "namespacedName": "mcp_slack_send_message"},
                    {"name": "list_channels", "namespacedName": "mcp_slack_list_channels"},
                ],
            },
        }
        created, failed = await _create_mcp_server_edges(
            "agent-1", mcp_servers, user_info, "user-key-1", mock_graph_provider, mock_logger
        )
        assert len(created) == 2
        assert failed == []
        all_tool_names = []
        for server in created:
            for tool in server.get("tools", []):
                all_tool_names.append(tool["name"])
        assert "create_issue" in all_tool_names
        assert "send_message" in all_tool_names
        assert "list_channels" in all_tool_names

    @pytest.mark.asyncio
    async def test_passes_transaction_id(self, mock_graph_provider, user_info, mock_logger):
        from app.api.routes.agent import _create_mcp_server_edges

        mcp_servers = {
            "github": {
                "displayName": "GitHub",
                "type": "github",
                "tools": [{"name": "t1", "namespacedName": "mcp_github_t1"}],
            }
        }
        await _create_mcp_server_edges(
            "agent-1", mcp_servers, user_info, "user-key-1",
            mock_graph_provider, mock_logger, transaction="txn-123"
        )
        for call in mock_graph_provider.batch_upsert_nodes.call_args_list:
            assert call.kwargs.get("transaction") == "txn-123"
        for call in mock_graph_provider.batch_create_edges.call_args_list:
            assert call.kwargs.get("transaction") == "txn-123"


# ============================================================================
# build_initial_state MCP fields
# ============================================================================


class TestBuildInitialStateMcp:
    """Tests that build_initial_state populates MCP fields correctly.

    NOTE: build_initial_state lives in chat_state.py which has a shallow
    import chain (langchain_core only). It is tested here to keep all
    MCP-related tests in one file.
    """

    @pytest.fixture
    def base_chat_query(self):
        return {
            "query": "test query",
            "limit": 10,
            "quickMode": False,
            "chatMode": "standard",
            "retrievalMode": "HYBRID",
        }

    @pytest.fixture
    def user_info(self):
        return {"userId": "u-1", "orgId": "o-1", "userEmail": "test@example.com"}

    @pytest.fixture
    def mock_services(self):
        return {
            "llm": MagicMock(),
            "logger": MagicMock(),
            "retrieval_service": MagicMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": MagicMock(),
        }

    def test_mcp_fields_empty_by_default(self, base_chat_query, user_info, mock_services):
        from app.modules.agents.qna.chat_state import build_initial_state

        state = build_initial_state(
            base_chat_query, user_info,
            mock_services["llm"], mock_services["logger"],
            mock_services["retrieval_service"], mock_services["graph_provider"],
            mock_services["reranker_service"], mock_services["config_service"],
        )
        assert state["agent_mcp_servers"] == []
        assert state["mcp_server_configs"] == {}
        assert state["tool_to_mcp_server_map"] == {}

    def test_mcp_servers_populated_from_query(self, base_chat_query, user_info, mock_services):
        from app.modules.agents.qna.chat_state import build_initial_state

        mcp_servers = [
            {"name": "github", "instanceId": "inst-1", "tools": [{"name": "create_issue"}]},
        ]
        mcp_configs = {"inst-1": {"transport": "stdio", "command": "npx github-mcp"}}
        base_chat_query["mcpServers"] = mcp_servers
        base_chat_query["mcpServerConfigs"] = mcp_configs

        state = build_initial_state(
            base_chat_query, user_info,
            mock_services["llm"], mock_services["logger"],
            mock_services["retrieval_service"], mock_services["graph_provider"],
            mock_services["reranker_service"], mock_services["config_service"],
        )
        assert state["agent_mcp_servers"] == mcp_servers
        assert state["mcp_server_configs"] == mcp_configs


# ============================================================================
# _load_mcp_tools
# ============================================================================


class TestLoadMcpTools:
    """Tests for _load_mcp_tools in tool_system.py.

    _load_mcp_tools only uses app.agents.mcp.models and app.agents.mcp.wrapper.
    However tool_system.py also imports RegistryToolWrapper from the heavy
    factories chain. Since we've stubbed app.agents.tools.* above, we can
    import tool_system — but we need to ensure _load_mcp_tools itself uses
    the *real* MCP wrapper. We import tool_system.py lazily here so that our
    stubs are in place.
    """

    def test_returns_empty_when_no_mcp_servers(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {"agent_mcp_servers": [], "mcp_server_configs": {}}
        assert _load_mcp_tools(state) == []

    def test_returns_empty_when_mcp_servers_missing(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {}
        assert _load_mcp_tools(state) == []

    def test_creates_wrappers_for_configured_tools(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {
            "agent_mcp_servers": [
                {
                    "name": "github",
                    "instanceId": "inst-1",
                    "tools": [
                        {
                            "name": "create_issue",
                            "namespacedName": "mcp_github_create_issue",
                            "description": "Create an issue",
                        },
                        {
                            "name": "list_repos",
                            "namespacedName": "mcp_github_list_repos",
                            "description": "List repositories",
                        },
                    ],
                }
            ],
            "mcp_server_configs": {"inst-1": {"transport": "stdio", "command": "npx mcp-github"}},
        }
        tools = _load_mcp_tools(state)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "mcp_github_create_issue" in names
        assert "mcp_github_list_repos" in names

    def test_skips_servers_with_no_tools(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {
            "agent_mcp_servers": [
                {"name": "empty_server", "instanceId": "inst-2", "tools": []},
            ],
            "mcp_server_configs": {},
        }
        assert _load_mcp_tools(state) == []

    def test_multiple_servers(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {
            "agent_mcp_servers": [
                {
                    "name": "github",
                    "instanceId": "inst-1",
                    "tools": [{"name": "create_issue", "namespacedName": "mcp_github_create_issue", "description": "desc"}],
                },
                {
                    "name": "slack",
                    "instanceId": "inst-2",
                    "tools": [{"name": "send_message", "namespacedName": "mcp_slack_send_message", "description": "desc"}],
                },
            ],
            "mcp_server_configs": {},
        }
        tools = _load_mcp_tools(state)
        assert len(tools) == 2

    def test_wrapper_has_correct_metadata(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {
            "agent_mcp_servers": [
                {
                    "name": "github",
                    "instanceId": "inst-1",
                    "tools": [
                        {
                            "name": "create_issue",
                            "namespacedName": "mcp_github_create_issue",
                            "description": "Create a GitHub issue",
                            "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
                        }
                    ],
                }
            ],
            "mcp_server_configs": {"inst-1": {"transport": "stdio"}},
        }
        tools = _load_mcp_tools(state)
        assert len(tools) == 1
        wrapper = tools[0]
        assert wrapper.name == "mcp_github_create_issue"
        assert wrapper.description == "Create a GitHub issue"
        assert wrapper.mcp_server_name == "github"
        assert wrapper.mcp_tool_name == "create_issue"
        assert wrapper.mcp_instance_id == "inst-1"

    def test_non_dict_tool_data_skipped(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {
            "agent_mcp_servers": [
                {
                    "name": "github",
                    "instanceId": "inst-1",
                    "tools": ["bad_string", None, {"name": "valid_tool", "namespacedName": "mcp_github_valid_tool", "description": "ok"}],
                }
            ],
            "mcp_server_configs": {},
        }
        tools = _load_mcp_tools(state)
        assert len(tools) == 1
        assert tools[0].name == "mcp_github_valid_tool"

    def test_null_input_schema_uses_default_schema(self):
        from app.modules.agents.qna.tool_system import _load_mcp_tools

        state = {
            "agent_mcp_servers": [
                {
                    "name": "jira",
                    "instanceId": "inst-jira",
                    "tools": [
                        {
                            "name": "create_issue",
                            "namespacedName": "mcp_jira_create_issue",
                            "description": "Create Jira issue",
                            "inputSchema": None,
                        }
                    ],
                }
            ],
            "mcp_server_configs": {"inst-jira": {"transport": "streamable_http", "url": "https://example.com/mcp"}},
        }

        tools = _load_mcp_tools(state)
        assert len(tools) == 1
        assert tools[0].mcp_input_schema == {"type": "object", "properties": {}}
