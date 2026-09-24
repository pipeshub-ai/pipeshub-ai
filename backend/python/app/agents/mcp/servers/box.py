"""Box MCP server template — official remote server (Streamable HTTP), OAuth, no DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="box",
        display_name="Box",
        description="Search, summarize, and extract insights from enterprise content in Box.",
        icon="/icons/connectors/box.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.box.com",
        # Confirmed live via https://mcp.box.com/.well-known/oauth-protected-resource -> AS
        # https://api.box.com, whose oauth-authorization-server metadata has no
        # `registration_endpoint` — Box requires an admin to create Integration Credentials
        # (a static OAuth app) in the Box Admin Console rather than dynamic client
        # registration. These are only the UI-hint defaults; the authorize flow always
        # re-discovers live (see dcr.py).
        authorization_url="https://account.box.com/api/oauth2/authorize",
        token_url="https://api.box.com/oauth2/token",
        default_scopes=["root_readwrite", "ai.readwrite"],
        supports_dcr=False,
        documentation_url="https://developer.box.com/guides/box-mcp/setup",
        auth_hint=AuthHint(
            label="Box OAuth",
            help_text=(
                "Create Integration Credentials for the Box MCP Server in your Box Admin "
                "Console (Integrations), then provide the client ID/secret below — Box does "
                "not support dynamic client registration."
            ),
        ),
        tags=["storage", "docs"],
    )
)
class BoxMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
