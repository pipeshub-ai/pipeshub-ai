"""DocuSign MCP server template — official remote server (Streamable HTTP), OAuth, no DCR.

Production endpoint. A developer/demo account cannot use this card (different host);
those testers add a custom server to https://mcp-d.docusign.com/mcp.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="docusign",
        display_name="DocuSign",
        description="Agreements, envelopes, and e-signature workflows via DocuSign IAM.",
        icon="/icons/connectors/docusign.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.docusign.com/mcp",
        # Confirmed live 2026-10-30 via discover_oauth_metadata("https://mcp.docusign.com/mcp"):
        # issuer https://account.docusign.com, no registration_endpoint, PKCE S256.
        # DocuSign requires an Integration Key (a static OAuth app) under Apps and Keys.
        # These are only the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://account.docusign.com/oauth/auth",
        token_url="https://account.docusign.com/oauth/token",
        default_scopes=[
            "signature",
            "aow_manage",
            "adm_store_unified_repo_read",
            "spring_read",
            "spring_write",
            "cors",
        ],
        supports_dcr=False,
        documentation_url="https://developers.docusign.com/platform/mcp-server/",
        auth_hint=AuthHint(
            label="DocuSign OAuth",
            help_text=(
                "Create an Integration Key (App and Keys) in your DocuSign production account "
                "and provide the client ID/secret below — DocuSign does not support dynamic "
                "client registration."
            ),
        ),
        tags=["e-signature", "docs", "contracts"],
    )
)
class DocuSignMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
