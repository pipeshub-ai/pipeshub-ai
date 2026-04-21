"""
Built-in MCP server catalog.

Each template defines a well-known MCP server type with its default command,
arguments, required/optional environment variables, and documentation.
Admins create *instances* from these templates (filling in credentials) or
register fully custom servers.
"""

from __future__ import annotations

from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport

BUILTIN_CATALOG: dict[str, MCPServerTemplate] = {
    # "jira": MCPServerTemplate(
    #     type_id="jira",
    #     display_name="Jira (Atlassian)",
    #     description=(
    #         "Atlassian Jira integration via the remote Atlassian MCP server. "
    #         "Search, create, and update issues, manage sprints, and query boards. "
    #         "Uses OAuth 2.1 with dynamic client registration — a browser will "
    #         "open for authorization on first use."
    #     ),
    #     transport=MCPTransport.STREAMABLE_HTTP,
    #     url="https://mcp.atlassian.com/v1/mcp",
    #     auth_mode=MCPAuthMode.OAUTH,
    #     supported_auth_types=["OAUTH"],
    #     required_env=[],
    #     optional_env=[],
    #     redirect_uri="mcp-servers/oauth/callback/jira",
    #     icon_path="/assets/icons/mcp-servers/jira.svg",
    #     documentation_url="https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server",
    #     tags=["atlassian", "project-management", "issue-tracking"],
    #     auth=AuthHint(
    #         methods=["oauth2"],
    #         default_method="oauth2",
    #         oauth2_authorization_url="https://auth.atlassian.com/authorize",
    #         oauth2_token_url="https://auth.atlassian.com/oauth/token",
    #         oauth2_scopes=[
    #             "read:me",
    #             "read:account",
    #             "read:jira-work",
    #             "write:jira-work",
    #             "read:jira-user",
    #             "offline_access",
    #         ],
    #         env_mapping={},
    #     ),
    # ),
    # "slack": MCPServerTemplate(
    #     type_id="slack",
    #     display_name="Slack",
    #     description=(
    #         "Official Slack MCP server. Search messages, channels, files, "
    #         "and users; send messages; read channel history and threads; "
    #         "create and manage canvases. Uses OAuth 2.0 — a browser will "
    #         "open for authorization on first use."
    #     ),
    #     transport=MCPTransport.STREAMABLE_HTTP,
    #     url="https://mcp.slack.com/mcp",
    #     auth_mode=MCPAuthMode.OAUTH,
    #     supported_auth_types=["OAUTH"],
    #     required_env=[],
    #     optional_env=[],
    #     redirect_uri="mcp-servers/oauth/callback/slack",
    #     icon_path="/assets/icons/mcp-servers/slack.svg",
    #     documentation_url="https://docs.slack.dev/ai/mcp-server",
    #     tags=["messaging", "communication", "collaboration"],
    #     auth=AuthHint(
    #         methods=["oauth2"],
    #         default_method="oauth2",
    #         oauth2_authorization_url="https://slack.com/oauth/v2_user/authorize",
    #         oauth2_token_url="https://slack.com/api/oauth.v2.user.access",
    #         oauth2_scopes=[
    #             "search:read.public",
    #             "search:read.private",
    #             "search:read.mpim",
    #             "search:read.im",
    #             "search:read.files",
    #             "search:read.users",
    #             "chat:write",
    #             "channels:history",
    #             "groups:history",
    #             "mpim:history",
    #             "im:history",
    #             "canvases:read",
    #             "canvases:write",
    #             "users:read",
    #             "users:read.email",
    #         ],
    #         env_mapping={},
    #     ),
    # ),
    "brave_search": MCPServerTemplate(
        type_id="brave_search",
        display_name="Brave Search",
        description=(
            "Brave Search API integration providing comprehensive search capabilities "
            "including web search, local business search, image search, video search, "
            "news search, and AI-powered summarization. Requires a Brave Search API key."
        ),
        transport=MCPTransport.STDIO,
        command="npx",
        default_args=["-y", "@brave/brave-search-mcp-server"],
        auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_types=["API_TOKEN"],
        required_env=["BRAVE_API_KEY"],
        optional_env=[
            "BRAVE_MCP_TRANSPORT",
            "BRAVE_MCP_PORT",
            "BRAVE_MCP_HOST",
            "BRAVE_MCP_LOG_LEVEL",
            "BRAVE_MCP_ENABLED_TOOLS",
            "BRAVE_MCP_DISABLED_TOOLS",
        ],
        icon_path="/assets/icons/mcp-servers/brave.svg",
        documentation_url="https://github.com/brave/brave-search-mcp-server",
        tags=["search", "web-search", "news", "images", "videos", "local-search"],
        auth=AuthHint(
            methods=["api_key"],
            default_method="api_key",
            env_mapping={"apiKey": "BRAVE_API_KEY"},
        ),
    ),
    "exa": MCPServerTemplate(
        type_id="exa",
        display_name="Exa Search",
        description=(
            "Exa web search and content retrieval via the remote Exa MCP server. "
            "Search the web by meaning, fetch and read full page content as clean "
            "markdown, and perform advanced filtered searches. Requires an Exa API key."
        ),
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.exa.ai/mcp",
        auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_types=["API_TOKEN"],
        required_env=[],
        optional_env=[],
        icon_path="/assets/icons/mcp-servers/exa.svg",
        documentation_url="https://exa.ai/docs/reference/exa-mcp",
        tags=["search", "web-search", "research"],
        auth=AuthHint(
            methods=["api_key"],
            default_method="api_key",
            env_mapping={"apiToken": "x-api-key"},
        ),
    ),
}


def get_template(type_id: str) -> MCPServerTemplate | None:
    """Get a catalog template by type_id."""
    return BUILTIN_CATALOG.get(type_id)


def list_templates() -> list[MCPServerTemplate]:
    """Return all catalog templates."""
    return list(BUILTIN_CATALOG.values())


def search_templates(query: str) -> list[MCPServerTemplate]:
    """Search templates by name, description, or tags."""
    q = query.lower()
    results: list[MCPServerTemplate] = []
    for tpl in BUILTIN_CATALOG.values():
        if (
            q in tpl.type_id.lower()
            or q in tpl.display_name.lower()
            or q in tpl.description.lower()
            or any(q in tag for tag in tpl.tags)
        ):
            results.append(tpl)
    return results
