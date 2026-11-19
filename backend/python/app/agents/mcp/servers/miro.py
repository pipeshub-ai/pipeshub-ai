"""Miro MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="miro",
        display_name="Miro",
        description="Collaborate on visual boards, diagrams, wireframes, and mind maps in Miro.",
        icon="/icons/connectors/miro.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.miro.com/",
        # Confirmed live via discovery against https://mcp.miro.com/ on 2026-11-18:
        # 401 returns protected resource metadata and AS https://mcp.miro.com/.
        # Authorization server advertises registration_endpoint (RFC 7591 DCR),
        # code_challenge_methods_supported=['S256'], and scopes: boards:read, boards:write.
        authorization_url="https://mcp.miro.com/authorize",
        token_url="https://mcp.miro.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developers.miro.com/docs/mcp-intro",
        auth_hint=AuthHint(
            label="Miro OAuth",
            help_text="Sign in with your Miro account.",
        ),
        tags=["miro", "whiteboard", "diagrams", "collaboration", "visual"],
    )
)
class MiroMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
