"""Slack MCP server template — Slack's own hosted server (Streamable HTTP).

Slack doesn't register clients on the fly: each workspace's admin creates an internal Slack app
with the Model Context Protocol setting and PKCE on, adds this deployment's redirect URI and the
user scopes, and enters its client ID and secret on the server. The scopes requested at sign-in
are the ones the server publishes in its protected-resource metadata.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="slack_official",
        display_name="Slack",
        description="Search messages and files, read channels and threads, and post in Slack, as the signed-in user.",
        icon="/icons/connectors/slack.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.slack.com/mcp",
        authorization_url="https://slack.com/oauth/v2_user/authorize",
        token_url="https://slack.com/api/oauth.v2.user.access",
        supports_dcr=False,
        documentation_url="https://docs.slack.dev/ai/slack-mcp-server/",
        tags=["chat", "collaboration"],
    )
)
class SlackOfficialMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
