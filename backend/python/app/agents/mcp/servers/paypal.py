"""PayPal MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="paypal",
        display_name="PayPal",
        description="Orders, payments, invoices, disputes, and subscriptions via PayPal.",
        icon="/icons/connectors/paypal.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.paypal.com/mcp",
        # Confirmed live via https://mcp.paypal.com/.well-known/oauth-protected-resource/mcp
        # -> AS https://mcp.paypal.com, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR). These are only the UI-hint defaults; the
        # authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.paypal.com/authorize",
        token_url="https://mcp.paypal.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developer.paypal.com/tools/mcp-server/",
        auth_hint=AuthHint(
            label="PayPal OAuth",
            help_text="Sign in with your PayPal account.",
        ),
        tags=["payments", "finance"],
    )
)
def register_paypal_server() -> None:
    """Register the official PayPal MCP server template with dynamic client registration."""
    pass
