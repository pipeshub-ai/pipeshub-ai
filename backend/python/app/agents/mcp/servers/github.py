"""GitHub MCP server template — official remote server (Streamable HTTP)."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="github",
        display_name="GitHub",
        description="Repositories, issues, pull requests, and actions on GitHub.",
        icon="/icons/connectors/github.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.API_TOKEN, MCPAuthMode.OAUTH],
        default_url="https://api.githubcopilot.com/mcp/",
        authorization_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        default_scopes=["repo", "read:org"],
        supports_dcr=False,
        documentation_url="https://github.com/github/github-mcp-server",
        auth_hint=AuthHint(
            label="Personal access token",
            placeholder="ghp_...",
            help_text="Create a fine-grained PAT at https://github.com/settings/tokens, or sign in with OAuth.",
        ),
        tags=["dev", "vcs"],
    )
)
class GitHubMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
