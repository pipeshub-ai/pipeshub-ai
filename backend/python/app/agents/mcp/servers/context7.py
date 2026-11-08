"""Context7 MCP server template — Upstash's official remote server (Streamable HTTP), no sign-in."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="context7",
        display_name="Context7",
        description="Up-to-date documentation and code examples for programming libraries and frameworks.",
        icon="/icons/connectors/context7.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.NONE,
        supported_auth_modes=[MCPAuthMode.NONE],
        # Confirmed live: `initialize` and `tools/list` answer without a token, at Context7's
        # anonymous rate limit. Its two tools (resolve-library-id, query-docs) mark themselves
        # read-only. The server also offers an API key (`CONTEXT7_API_KEY` header) and OAuth
        # for higher limits; this entry doesn't use either yet.
        default_url="https://mcp.context7.com/mcp",
        documentation_url="https://context7.com/docs",
        tags=["docs", "developer", "libraries"],
    )
)
class Context7MCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
