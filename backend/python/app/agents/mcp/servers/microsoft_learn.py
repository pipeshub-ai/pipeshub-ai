"""Microsoft Learn MCP server template — Microsoft's official remote server (Streamable HTTP), no sign-in."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="microsoft_learn",
        display_name="Microsoft Learn",
        description="Search Microsoft's official documentation and code samples for Azure, .NET, Microsoft 365 and more.",
        icon="/icons/connectors/microsoft-learn.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.NONE,
        supported_auth_modes=[MCPAuthMode.NONE],
        # Confirmed live: `initialize` and `tools/list` answer without a token, and the server
        # publishes no OAuth metadata. Its three tools (microsoft_docs_search,
        # microsoft_docs_fetch, microsoft_code_sample_search) mark themselves read-only.
        default_url="https://learn.microsoft.com/api/mcp",
        documentation_url="https://learn.microsoft.com/en-us/training/support/mcp",
        tags=["docs", "microsoft", "azure", "developer"],
    )
)
class MicrosoftLearnMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
