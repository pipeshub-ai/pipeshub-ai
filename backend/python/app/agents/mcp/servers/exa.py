"""Exa MCP server template — AI-native web search, run over STDIO."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="exa",
        display_name="Exa",
        description="Neural web search and content retrieval for AI agents.",
        icon="/icons/connectors/exa.svg",
        transport=MCPTransport.STDIO,
        default_auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_modes=[MCPAuthMode.API_TOKEN],
        command="npx",
        args=["-y", "exa-mcp-server"],
        required_env=["EXA_API_KEY"],
        documentation_url="https://docs.exa.ai/reference/exa-mcp",
        auth_hint=AuthHint(
            label="Exa API key",
            placeholder="exa_...",
            help_text="Create an API key at https://dashboard.exa.ai/",
        ),
        tags=["search", "web"],
    )
)
class ExaMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
