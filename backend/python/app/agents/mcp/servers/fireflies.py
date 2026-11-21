"""Fireflies MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

This is the account-data server. Fireflies also runs a docs-search server at
https://docs.fireflies.ai/mcp, which is not part of this entry.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import (
    AuthHint,
    MCPAuthMode,
    MCPCategory,
    MCPServerTemplate,
    MCPTransport,
)


@mcp_server(
    MCPServerTemplate(
        type_id="fireflies",
        display_name="Fireflies",
        description="Search meeting transcripts, summaries and action items in Fireflies.",
        icon="/icons/connectors/fireflies.png",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://api.fireflies.ai/mcp",
        # Confirmed live 2026-11-18: an unauthenticated POST returns 401 with
        # `scope="email profile"` and `resource_metadata=
        # https://api.fireflies.ai/.well-known/oauth-protected-resource/mcp`. The authorization
        # server is https://api.fireflies.ai, whose metadata has a `registration_endpoint`
        # (https://api.fireflies.ai/register, RFC 7591), S256 PKCE only, token auth
        # client_secret_post/none, and CIMD support. PipesHub's DCR registration with a callback
        # redirect URI was accepted.
        authorization_url="https://api.fireflies.ai/authorize",
        token_url="https://api.fireflies.ai/token",
        # The 401 asks for `email profile`; the server also offers `meetings.upload`, which this
        # entry does not need, so leave this empty and let the 401 decide.
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://docs.fireflies.ai/getting-started/mcp-configuration",
        auth_hint=AuthHint(
            label="Fireflies OAuth",
            help_text="Sign in with your Fireflies account.",
        ),
        tags=["meetings", "transcripts", "notes"],
        category=MCPCategory.MEETINGS,
        capabilities=[
            "Search meeting transcripts",
            "Get action items from calls",
            "Summarize a meeting",
        ],
        read_only=True,
    )
)
class FirefliesMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
