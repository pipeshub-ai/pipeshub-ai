"""Apollo's hosted Streamable HTTP MCP server with OAuth and dynamic registration."""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="apollo",
        display_name="Apollo",
        description="Find people and companies in Apollo.",
        icon="/icons/connectors/apollo.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.apollo.io/mcp",
        # Discovery confirmed these endpoints and DCR via https://mcp.apollo.io/.well-known/oauth-authorization-server.
        authorization_url="https://mcp.apollo.io/mcp/oauth_metadata/redirect_to_authorize",
        token_url="https://mcp.apollo.io/api/v1/oauth/token",
        # The server's challenge includes write and outreach scopes. Start with profile and search access.
        default_scopes=[
            "read_user_profile",
            "mcp_scope",
            "mixed_people_api_search",
            "mixed_companies_search",
        ],
        supports_dcr=True,
        documentation_url="https://docs.apollo.io/docs/apollo-mcp",
        tags=["sales", "prospecting", "crm"],
    )
)
class ApolloMCPServer:
    """Register the Apollo catalog template."""
