"""Hugging Face MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="huggingface",
        display_name="Hugging Face",
        description="Search models, datasets, Spaces and papers on the Hugging Face Hub.",
        icon="/icons/connectors/huggingface.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        # `?login` makes the server ask for a sign-in; without it, it answers anonymously
        # with only the public tools.
        default_url="https://huggingface.co/mcp?login",
        # Confirmed live: the 401 names
        # https://huggingface.co/.well-known/oauth-protected-resource/mcp?login -> AS
        # https://huggingface.co, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR), S256 PKCE and CIMD support. These are only
        # the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://huggingface.co/oauth/authorize",
        token_url="https://huggingface.co/oauth/token",
        # Read-only. The server's metadata also lists `contribute-repos`, `jobs` and
        # `inference-api`; confirmed live that without them all six tools are still listed
        # and the consent page asks only for "Read repositories" and "MCP tools".
        default_scopes=["openid", "profile", "read-mcp", "read-repos"],
        supports_dcr=True,
        documentation_url="https://huggingface.co/docs/hub/en/hf-mcp-server",
        auth_hint=AuthHint(
            label="Hugging Face OAuth",
            help_text="Sign in with your Hugging Face account.",
        ),
        tags=["ml", "models", "datasets", "ai"],
    )
)
class HuggingFaceMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
