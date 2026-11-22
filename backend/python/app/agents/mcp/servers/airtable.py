"""Airtable MCP template — official hosted server, Streamable HTTP and OAuth DCR."""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import (
    AuthHint,
    MCPAuthMode,
    MCPServerTemplate,
    MCPTransport,
)


@mcp_server(
    MCPServerTemplate(
        type_id="airtable",
        display_name="Airtable",
        description="Discover bases, explore tables, and read and update Airtable records.",
        icon="/icons/connectors/airtable.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.airtable.com/mcp",
        # Confirmed via mcp.airtable.com/.well-known/oauth-protected-resource/mcp
        # -> AS https://airtable.com/oauth2/v1. Live discovery advertises DCR,
        # S256 and client_secret_basic/none; these endpoints are fallback hints.
        authorization_url="https://airtable.com/oauth2/v1/authorize",
        token_url="https://airtable.com/oauth2/v1/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://airtable.com/developers/agents/mcp/getting-started",
        auth_hint=AuthHint(
            label="Airtable OAuth",
            help_text="Sign in with Airtable and choose the bases to connect.",
        ),
        tags=["database", "productivity"],
    )
)
class AirtableMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
