"""Trello MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="trello",
        display_name="Trello",
        description="Read and update boards, lists and cards in Trello.",
        icon="/icons/connectors/trello.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        # Trello's own hosted server, separate from Atlassian Rovo (`atlassian_rovo.py`),
        # which doesn't cover Trello.
        default_url="https://mcp.trello.com/v1",
        # Confirmed live: the 401 names
        # https://mcp.trello.com/.well-known/oauth-protected-resource/v1 -> AS
        # https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3, whose
        # oauth-authorization-server metadata advertises a `registration_endpoint`
        # (RFC 7591 DCR), S256 PKCE and CIMD support. These are only the UI-hint defaults;
        # the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
        # The 401 names no scopes, so sign-in asks for the server's protected-resource
        # `scopes_supported`: read and write on boards, organizations, members, inbox and
        # planner, plus `offline_access` for a refresh token.
        default_scopes=[],
        supports_dcr=True,
        # Atlassian hasn't published a docs page for the Trello MCP server yet.
        documentation_url="https://claude.com/connectors/trello",
        auth_hint=AuthHint(
            label="Trello OAuth",
            help_text="Sign in with your Trello (Atlassian) account.",
        ),
        tags=["boards", "cards", "tasks", "project-management"],
    )
)
class TrelloMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
