"""Square MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="square",
        display_name="Square",
        description="Payments, orders, items, customers, and business data in Square.",
        icon="/icons/connectors/square.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.squareup.com/mcp",
        # Confirmed live via https://mcp.squareup.com/.well-known/oauth-protected-resource/mcp
        # -> AS https://mcp.squareup.com, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR) and S256 PKCE. These are only the UI-hint
        # defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.squareup.com/authorize",
        token_url="https://mcp.squareup.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developer.squareup.com/docs/mcp",
        auth_hint=AuthHint(
            label="Square OAuth",
            help_text="Sign in with your Square account.",
        ),
        tags=["payments", "commerce", "orders", "pos"],
    )
)
def register_square_server() -> None:
    """Register the official Square MCP server template with dynamic client registration."""
    pass
