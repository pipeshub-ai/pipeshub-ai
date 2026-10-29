"""DeepWiki MCP server template — Cognition's official remote server (Streamable HTTP), no sign-in."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="deepwiki",
        display_name="DeepWiki",
        description="Ask questions about public GitHub repositories and read the documentation DeepWiki generates for them.",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.NONE,
        supported_auth_modes=[MCPAuthMode.NONE],
        # Confirmed live: `initialize` and `tools/list` answer without a token. It covers public
        # repositories only (private ones need Devin's signed-in server, a different product).
        # Its three tools (read_wiki_structure, read_wiki_contents, ask_wiki_question) carry no
        # read-only hint, so they ask for approval until someone pre-approves them.
        default_url="https://mcp.deepwiki.com/mcp",
        documentation_url="https://docs.devin.ai/work-with-devin/deepwiki-mcp",
        tags=["docs", "github", "developer"],
    )
)
class DeepWikiMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
