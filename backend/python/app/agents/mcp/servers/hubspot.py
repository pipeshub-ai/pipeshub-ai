"""HubSpot MCP server template — official remote server (Streamable HTTP), OAuth, no DCR.

This is the CRM-data server (`mcp.hubspot.com`), distinct from HubSpot's separate, CLI-based
Developer MCP server used for scaffolding apps/CMS assets locally.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="hubspot",
        display_name="HubSpot",
        description="Contacts, companies, deals, and activity history in the HubSpot CRM.",
        icon="/icons/connectors/hubspot.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.hubspot.com",
        # Confirmed live 2026-11-06 via discover_oauth_metadata('https://mcp.hubspot.com'):
        # authorization_endpoint https://mcp.hubspot.com/oauth/authorize/user, token_endpoint
        # https://mcp.hubspot.com/oauth/v3/token, registration_endpoint null, PKCE S256,
        # client_id_metadata_document_supported false — HubSpot requires an admin to create an
        # MCP connector (static OAuth client) under Development > MCP Connectors rather than
        # dynamic client registration. These are only the UI-hint defaults; the authorize flow
        # always re-discovers live (see dcr.py).
        authorization_url="https://mcp.hubspot.com/oauth/authorize/user",
        token_url="https://mcp.hubspot.com/oauth/v3/token",
        default_scopes=[],
        supports_dcr=False,
        require_oauth_client=True,
        documentation_url="https://developers.hubspot.com/ai-tools/mcp",
        auth_hint=AuthHint(
            label="HubSpot OAuth",
            help_text=(
                "Create an MCP connector in your HubSpot account (Development > MCP Connectors), "
                "add the redirect URI shown on this form exactly (must match, including HTTPS "
                "when this deployment's public URL is HTTPS), then paste that connector's "
                "client ID and secret — HubSpot does not support dynamic client registration. "
                "All actions respect the connecting user's existing HubSpot permissions."
            ),
        ),
        tags=["crm", "sales", "marketing"],
    )
)
class HubSpotMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
