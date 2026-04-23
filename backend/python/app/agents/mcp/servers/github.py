"""
GitHub MCP Server Template

Official GitHub MCP server integration via the github-mcp-server package.
Supports both OAuth 2.0 and Personal Access Token authentication.
"""

from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


@MCPServer(
    type_id="github",
    display_name="GitHub",
    description=(
        "Official GitHub MCP server. Create and manage repositories, files, "
        "issues, pull requests, branches, and workflows. Search code, issues, "
        "and users. Supports OAuth 2.0 or Personal Access Token authentication."
    ),
    transport=MCPTransport.STREAMABLE_HTTP,
    url="https://api.githubcopilot.com/mcp/",
    auth_mode=MCPAuthMode.OAUTH,
    supported_auth_types=["OAUTH", "API_TOKEN"],
    required_env=["GITHUB_PERSONAL_ACCESS_TOKEN"],
    optional_env=[],
    redirect_uri="mcp-servers/oauth/callback/github",
    icon_path="/assets/icons/mcp-servers/github.svg",
    documentation_url="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    tags=["development", "git", "version-control", "code", "issues", "pull-requests"],
    auth=AuthHint(
        methods=["oauth2", "api_key"],
        default_method="oauth2",
        oauth2_authorization_url="https://github.com/login/oauth/authorize",
        oauth2_token_url="https://github.com/login/oauth/access_token",
        oauth2_scopes=["repo", "read:org", "read:user", "user:email", "gist", "notifications"],
        env_mapping={"apiToken": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    ),
)
class GitHubMCP:
    """GitHub MCP server template."""
    pass
