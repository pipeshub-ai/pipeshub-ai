"""GitLab MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

Targets GitLab.com (`https://gitlab.com/api/v4/mcp`). Self-managed and GitLab Dedicated
instances serve the same MCP server on each customer's own domain (`https://<host>/api/v4/mcp`),
which admins can add as a custom server.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="gitlab",
        display_name="GitLab",
        description="Projects, issues, merge requests, and pipelines on GitLab (beta).",
        icon="/icons/connectors/gitlab.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://gitlab.com/api/v4/mcp",
        # Confirmed live 2026-11-07 via discover_oauth_metadata("https://gitlab.com/api/v4/mcp"):
        # 401 challenge scope="mcp" + https://gitlab.com/.well-known/oauth-protected-resource/api/v4/mcp
        # -> AS https://gitlab.com, whose oauth-authorization-server metadata advertises
        # registration_endpoint https://gitlab.com/oauth/register (RFC 7591 DCR) and PKCE S256.
        # Dynamic registration with /mcp-servers/oauth/callback/ succeeds (public client, auth_method=none).
        # These are only the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://gitlab.com/oauth/authorize",
        token_url="https://gitlab.com/oauth/token",
        default_scopes=["mcp"],
        supports_dcr=True,
        documentation_url="https://docs.gitlab.com/user/model_context_protocol/mcp_server/",
        auth_hint=AuthHint(
            label="GitLab OAuth",
            help_text="Sign in with your GitLab.com account. PipesHub registers itself; no GitLab OAuth app is required.",
        ),
        tags=["dev", "vcs", "issues"],
    )
)
class GitLabMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
