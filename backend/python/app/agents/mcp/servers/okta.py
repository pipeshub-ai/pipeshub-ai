"""Okta MCP server template — each org's own host (Streamable HTTP), OAuth.

Okta's MCP address is ``https://<org>.okta.com/mcp``, so this card asks for that
URL (``url_editable``). Sign-in discovers that org's authorize and token
endpoints. The org advertises a registration endpoint that rejects anonymous
clients, so the admin creates an OIDC app and pastes its client ID and secret
(``require_oauth_client``). Okta's authorize endpoint rejects a request that
names no scope (``invalid_scope``), and a 403 from ``/mcp`` publishes none, so
the card asks for read access to users and groups.
Confirmed live 2026-10-30 against an org host ``https://<org>.okta.com/mcp``:
its authorization server is that org's ``/oauth2/v1``, with PKCE S256 and a
``registration_endpoint`` that returns 403 ``E0000005`` without a session.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="okta",
        display_name="Okta",
        description="Users and groups in your Okta org. Setup asks for your org's MCP URL and an OAuth app.",
        icon="/icons/connectors/okta.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        url_editable=True,
        supports_dcr=False,
        require_oauth_client=True,
        default_scopes=["okta.users.read", "okta.groups.read"],
        documentation_url="https://help.okta.com/oie/en-us/content/topics/mcp/mcp-server.htm",
        auth_hint=AuthHint(
            label="Okta OAuth",
            help_text=(
                "Your org's MCP URL, for example https://your-company.okta.com/mcp. "
                "Create an OIDC app, add the redirect URI shown here, and paste its client ID and secret."
            ),
        ),
        tags=["identity", "iam", "admin"],
    )
)
class OktaMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
