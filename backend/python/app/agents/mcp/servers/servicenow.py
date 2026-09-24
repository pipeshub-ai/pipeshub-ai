# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """ServiceNow MCP server template — official native server (Streamable HTTP), OAuth, no DCR.

# Unlike GitHub/GitLab-style SaaS defaults, ServiceNow has no shared multi-tenant MCP host —
# every org provisions its own MCP Server Console entry (Zurich release or later) at a URL
# scoped to their instance. `default_url`/`authorization_url`/`token_url` below are therefore
# placeholder patterns the admin must replace with their own instance name before connecting;
# the authorize flow always re-discovers live against whatever host is configured (see dcr.py).
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="servicenow",
#         display_name="ServiceNow",
#         description="Incidents, requests, catalog items, and Now Assist skills across ITSM, HR, and CSM in ServiceNow.",
#         icon="/icons/connectors/servicenow.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://<your-instance>.service-now.com/sncapps/mcp-server/mcp/<server-name>",
#         # Per-instance OAuth 2.1: `<instance>.service-now.com/oauth_auth.do` and
#         # `/oauth_token.do` are ServiceNow's standard endpoint suffixes for any instance,
#         # confirmed against ServiceNow's own OAuth provider docs and the Zurich+ MCP setup
#         # walkthrough. No shared registration_endpoint exists — each org creates a static
#         # OAuth application registry entry (Client ID/secret) rather than DCR.
#         authorization_url="https://<your-instance>.service-now.com/oauth_auth.do",
#         token_url="https://<your-instance>.service-now.com/oauth_token.do",
#         default_scopes=[],
#         supports_dcr=False,
#         documentation_url="https://www.servicenow.com/community/product-launch-blogs/bring-the-servicenow-ai-platform-to-any-employee-experience-with/ba-p/3397470",
#         auth_hint=AuthHint(
#             label="ServiceNow OAuth",
#             help_text=(
#                 "Requires the Zurich release or later with the MCP Server Console enabled. "
#                 "Replace <your-instance> in the URL and endpoints with your ServiceNow instance "
#                 "name, publish an MCP server in AI Agent Studio, create an OAuth application "
#                 "registry entry for it, and provide the client ID/secret below — ServiceNow "
#                 "does not support dynamic client registration."
#             ),
#         ),
#         tags=["itsm", "helpdesk", "hr"],
#     )
# )
# class ServiceNowMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
