"""Atlassian Rovo MCP server template — official remote server (SSE), OAuth 3LO."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="atlassian_rovo",
        display_name="Atlassian Rovo",
        description="Jira, Confluence, Bitbucket, and more via the Atlassian Rovo MCP Server.",
        icon="/icons/connectors/atlassian.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.atlassian.com/v1/mcp",
        # The Rovo MCP server fronts its own AS (mcp.atlassian.com / cf.mcp.atlassian.com),
        # not auth.atlassian.com's direct 3LO endpoints, and supports RFC 7591 DCR —
        # confirmed by probing https://mcp.atlassian.com/.well-known/oauth-authorization-server.
        # Its metadata advertises no `scopes_supported`, and the 3LO `read:jira-work`-style
        # scopes below belong to direct Atlassian OAuth, not this AS — leave scopes empty
        # rather than send a value the MCP AS doesn't understand. These are only the UI-hint
        # defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.atlassian.com/v1/authorize",
        token_url="https://cf.mcp.atlassian.com/v1/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://support.atlassian.com/atlassian-rovo-mcp-server/",
        auth_hint=AuthHint(
            label="Atlassian OAuth",
            help_text="Sign in with your Atlassian account.",
        ),
        tags=["issues", "docs", "project-management"],
    )
)
class AtlassianRovoMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
