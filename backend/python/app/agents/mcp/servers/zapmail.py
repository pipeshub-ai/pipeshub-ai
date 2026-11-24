"""Zapmail's hosted Streamable HTTP MCP server with OAuth and dynamic registration."""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPCategory,
    MCPServerTemplate,
    MCPTransport,
)


@mcp_server(
    MCPServerTemplate(
        type_id="zapmail",
        display_name="Zapmail",
        description="Manage cold email domains and mailboxes in Zapmail.",
        icon="/icons/connectors/zapmail.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.zapmail.ai/mcp",
        # Discovery confirmed these endpoints and DCR via https://mcp.zapmail.ai/.well-known/oauth-authorization-server.
        authorization_url="https://mcp.zapmail.ai/authorize",
        token_url="https://mcp.zapmail.ai/token",
        # domains and workspaces_billing are opt-in: both hold tools that spend wallet balance (buy_domain, renew_domains,
        # purchase_*, upgrade_*). The default mailboxes grant still lets an agent provision, assign, update and schedule
        # removal of mailboxes and share mailbox analyses, so it is not read-only.
        default_scopes=["mailboxes", "inbox"],
        supports_dcr=True,
        documentation_url="https://docs.zapmail.ai/mcp-2107149m0",
        tags=["email", "outreach", "infrastructure"],
        category=MCPCategory.SALES,
        capabilities=[
            "Provision and update outreach mailboxes",
            "Read a mailbox's inbox",
            "Buy and renew sending domains",
        ],
    )
)
class ZapmailMCPServer:
    """Register the Zapmail catalog template."""
