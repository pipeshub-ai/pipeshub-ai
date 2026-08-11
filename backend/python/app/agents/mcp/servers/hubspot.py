# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """HubSpot MCP server template — official remote server (Streamable HTTP), OAuth, no DCR.

# This is the CRM-data server (`mcp.hubspot.com`), distinct from HubSpot's separate, CLI-based
# Developer MCP server used for scaffolding apps/CMS assets locally.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="hubspot",
#         display_name="HubSpot",
#         description="Contacts, companies, deals, and activity history in the HubSpot CRM.",
#         icon="/icons/connectors/hubspot.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.hubspot.com",
#         # Confirmed live via https://mcp.hubspot.com/.well-known/oauth-protected-resource -> AS
#         # https://mcp.hubspot.com, whose oauth-authorization-server metadata has no
#         # `registration_endpoint` — HubSpot requires an admin to create an "MCP Auth App" (a
#         # static OAuth client) under Development > MCP Auth Apps rather than dynamic client
#         # registration. These are only the UI-hint defaults; the authorize flow always
#         # re-discovers live (see dcr.py).
#         authorization_url="https://mcp.hubspot.com/oauth/authorize/user",
#         token_url="https://mcp.hubspot.com/oauth/v3/token",
#         default_scopes=[],
#         supports_dcr=False,
#         documentation_url="https://developers.hubspot.com/ai-tools/mcp",
#         auth_hint=AuthHint(
#             label="HubSpot OAuth",
#             help_text=(
#                 "Create an MCP Auth App in your HubSpot account (Development > MCP Auth Apps) "
#                 "and provide its client ID/secret below — HubSpot does not support dynamic "
#                 "client registration. All actions respect the connecting user's existing "
#                 "HubSpot permissions."
#             ),
#         ),
#         tags=["crm", "sales", "marketing"],
#     )
# )
# class HubSpotMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
