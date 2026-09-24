"""Slack MCP server template — community server, run over STDIO with a bot token."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="slack",
        display_name="Slack",
        description="Read and post messages, list channels and users in a Slack workspace.",
        icon="/icons/connectors/slack.svg",
        transport=MCPTransport.STDIO,
        default_auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_modes=[MCPAuthMode.API_TOKEN],
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
        required_env=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        optional_env=["SLACK_CHANNEL_IDS"],
        documentation_url="https://github.com/modelcontextprotocol/servers-archived/tree/main/src/slack",
        auth_hint=AuthHint(
            label="Slack bot token",
            placeholder="xoxb-...",
            help_text=(
                "Install a Slack app with the required scopes and copy its bot token (xoxb-...). "
                "Also provide your workspace Team ID (starts with T)."
            ),
        ),
        tags=["chat", "collaboration"],
    )
)
class SlackMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
