"""Slack community MCP server template — run over STDIO with a bot token. Replaced by Slack's
own hosted server (`slack_official`): the package is archived and npm marks it unsupported."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="slack",
        display_name="Slack (community)",
        description="The archived community Slack server, run with a bot token. No longer maintained.",
        icon="/icons/connectors/slack.svg",
        transport=MCPTransport.STDIO,
        default_auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_modes=[MCPAuthMode.API_TOKEN],
        command="npx",
        # The final release: the package is archived upstream and marked unsupported on npm.
        args=["-y", "@modelcontextprotocol/server-slack@2025.4.25"],
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
        replaced_by="slack_official",
    )
)
class SlackMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
