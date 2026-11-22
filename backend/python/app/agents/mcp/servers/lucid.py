"""Lucid MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="lucid",
        display_name="Lucid",
        description="Collaborate on diagrams, flowcharts, wireframes and whiteboards in Lucid.",
        icon="/icons/connectors/lucid.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.lucid.app/mcp",
        # Confirmed live via discovery against https://mcp.lucid.app/mcp:
        # 401 returns protected resource metadata and AS https://mcp.lucid.app.
        # Authorization server advertises registration_endpoint (RFC 7591 DCR),
        # code_challenge_methods_supported=['S256'], and CIMD support.
        authorization_url="https://mcp.lucid.app/oauth/authorize",
        token_url="https://mcp.lucid.app/oauth/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://help.lucid.co/hc/en-us/articles/42578801807508",
        auth_hint=AuthHint(
            label="Lucid OAuth",
            help_text="Sign in with your Lucid account.",
        ),
        tags=["lucid", "diagrams", "flowcharts", "whiteboard", "design"],
    )
)
class LucidMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
