"""PayPal MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

Production host only. PayPal's docs still show a `/http` path; that path returns 404,
and `https://mcp.paypal.com/mcp` is the endpoint that answers.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="paypal",
        display_name="PayPal",
        description="Invoices, payments, and merchant activity in a PayPal account.",
        icon="/icons/connectors/paypal.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.paypal.com/mcp",
        # Confirmed live 2026-11-05 via discover_oauth_metadata("https://mcp.paypal.com/mcp"):
        # 401 resource_metadata https://mcp.paypal.com/.well-known/oauth-protected-resource/mcp
        # -> resource https://mcp.paypal.com/mcp, authorization server https://mcp.paypal.com.
        # That server's oauth-authorization-server metadata advertises registration_endpoint
        # https://mcp.paypal.com/register (RFC 7591 DCR) and PKCE S256. A registration posted
        # as client_name PipesHub, with this product's /mcp-servers/oauth/callback/ redirect,
        # is accepted for both http://localhost and https. CIMD is not advertised.
        # These are only the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.paypal.com/authorize",
        token_url="https://mcp.paypal.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developer.paypal.com/tools/mcp-server/",
        auth_hint=AuthHint(
            label="PayPal OAuth",
            help_text="Sign in with your PayPal account. PipesHub registers itself; no PayPal app is required.",
        ),
        tags=["payments", "invoices", "finance"],
    )
)
class PayPalMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
