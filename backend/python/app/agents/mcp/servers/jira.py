"""
Jira (Atlassian) MCP Server Template

Atlassian Jira integration via the remote Atlassian MCP server.
"""

from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


@MCPServer(
    type_id="jira",
    display_name="Jira (Atlassian)",
    description=(
        "Atlassian Jira integration via the remote Atlassian MCP server. "
        "Search, create, and update issues, manage sprints, and query boards. "
        "Uses OAuth 2.1 with dynamic client registration — a browser will "
        "open for authorization on first use."
    ),
    transport=MCPTransport.STREAMABLE_HTTP,
    url="https://mcp.atlassian.com/v1/mcp",
    auth_mode=MCPAuthMode.OAUTH,
    supported_auth_types=["OAUTH"],
    required_env=[],
    optional_env=[],
    redirect_uri="mcp-servers/oauth/callback/jira",
    icon_path="/assets/icons/connectors/jira.svg",
    documentation_url="https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server",
    tags=["atlassian", "project-management", "issue-tracking"],
    auth=AuthHint(
        methods=["oauth2"],
        default_method="oauth2",
        oauth2_authorization_url="https://auth.atlassian.com/authorize",
        oauth2_token_url="https://auth.atlassian.com/oauth/token",
        oauth2_scopes=[
            "read:me",
            "read:account",
            "read:jira-work",
            "write:jira-work",
            "read:jira-user",
            "offline_access",
        ],
        env_mapping={},
    ),
)
class JiraMCP:
    """Jira MCP server template."""
    pass
