"""Unit tests for app.agents.constants.mcp_server_constants."""


class TestMCPServerConstants:
    """Tests for MCP server path helpers and enums."""

    def test_mcp_server_category_enum(self):
        from app.agents.constants.mcp_server_constants import MCPServerCategory

        assert MCPServerCategory.DEVELOPMENT.value == "development"
        assert MCPServerCategory.COMMUNICATION.value == "communication"
        assert MCPServerCategory.DATABASE.value == "database"
        assert MCPServerCategory.FILE_SYSTEM.value == "file_system"
        assert MCPServerCategory.PRODUCTIVITY.value == "productivity"
        assert MCPServerCategory.CUSTOM.value == "custom"

    def test_default_instances_path(self):
        from app.agents.constants.mcp_server_constants import (
            DEFAULT_MCP_SERVER_INSTANCES_PATH,
        )

        assert DEFAULT_MCP_SERVER_INSTANCES_PATH == "/services/mcp-server-instances"

    def test_get_mcp_server_config_path(self):
        from app.agents.constants.mcp_server_constants import (
            get_mcp_server_config_path,
        )

        assert (
            get_mcp_server_config_path("instanceId", "userId")
            == "/services/mcp-servers/instanceId/userId"
        )

    def test_get_mcp_server_instance_users_prefix(self):
        from app.agents.constants.mcp_server_constants import (
            get_mcp_server_instance_users_prefix,
        )

        assert (
            get_mcp_server_instance_users_prefix("instanceId")
            == "/services/mcp-servers/instanceId/"
        )

    def test_get_mcp_server_instances_path(self):
        from app.agents.constants.mcp_server_constants import (
            DEFAULT_MCP_SERVER_INSTANCES_PATH,
            get_mcp_server_instances_path,
        )

        assert get_mcp_server_instances_path() == DEFAULT_MCP_SERVER_INSTANCES_PATH

    def test_get_mcp_server_oauth_client_path(self):
        from app.agents.constants.mcp_server_constants import (
            get_mcp_server_oauth_client_path,
        )

        assert (
            get_mcp_server_oauth_client_path("instance-123")
            == "/services/mcp-servers/instance-123/oauth-client"
        )

    def test_normalize_mcp_server_type(self):
        from app.agents.constants.mcp_server_constants import (
            normalize_mcp_server_type,
        )

        assert normalize_mcp_server_type("  GitHub  ") == "github"
        assert normalize_mcp_server_type("SLACK") == "slack"

    def test_normalize_mcp_server_name(self):
        from app.agents.constants.mcp_server_constants import (
            normalize_mcp_server_name,
        )

        assert normalize_mcp_server_name("GitHub Server") == "github_server"

    def test_normalize_mcp_server_name_with_server_types(self):
        from app.agents.constants.mcp_server_constants import (
            normalize_mcp_server_name,
        )

        assert normalize_mcp_server_name("jira") == "jira"
        assert normalize_mcp_server_name("brave-search") == "brave_search"
        assert normalize_mcp_server_name("brave_search") == "brave_search"
        assert normalize_mcp_server_name("exa") == "exa"
        assert normalize_mcp_server_name("slack") == "slack"

    def test_normalize_mcp_server_name_for_tool_namespacing(self):
        """Verify the pattern used to build mcp_{serverType}_{toolName} keys."""
        from app.agents.constants.mcp_server_constants import (
            normalize_mcp_server_name,
        )

        server_type = "brave-search"
        tool_name = "search"
        assert f"mcp_{normalize_mcp_server_name(server_type)}_{tool_name}" == "mcp_brave_search_search"

        server_type = "jira"
        tool_name = "create_issue"
        assert f"mcp_{normalize_mcp_server_name(server_type)}_{tool_name}" == "mcp_jira_create_issue"
