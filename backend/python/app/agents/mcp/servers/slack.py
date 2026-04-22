"""
Slack MCP Server Template

Official Slack MCP server integration.
"""

from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


@MCPServer(
    type_id="slack",
    display_name="Slack",
    description=(
        "Official Slack MCP server. Search messages, channels, files, "
        "and users; send messages; read channel history and threads; "
        "create and manage canvases. Uses OAuth 2.0 — a browser will "
        "open for authorization on first use."
    ),
    transport=MCPTransport.STREAMABLE_HTTP,
    url="https://mcp.slack.com/mcp",
    auth_mode=MCPAuthMode.OAUTH,
    supported_auth_types=["OAUTH"],
    required_env=[],
    optional_env=[],
    redirect_uri="mcp-servers/oauth/callback/slack",
    icon_path="/assets/icons/mcp-servers/slack.svg",
    documentation_url="https://docs.slack.dev/ai/mcp-server",
    tags=["messaging", "communication", "collaboration"],
    auth=AuthHint(
        methods=["oauth2"],
        default_method="oauth2",
        oauth2_authorization_url="https://slack.com/oauth/v2_user/authorize",
        oauth2_token_url="https://slack.com/api/oauth.v2.user.access",
        oauth2_scopes=[
            "search:read.public",
            "search:read.private",
            "search:read.mpim",
            "search:read.im",
            "search:read.files",
            "search:read.users",
            "chat:write",
            "channels:history",
            "groups:history",
            "mpim:history",
            "im:history",
            "canvases:read",
            "canvases:write",
            "users:read",
            "users:read.email",
        ],
        env_mapping={},
    ),
)
class SlackMCP:
    """Slack MCP server template."""
    pass
