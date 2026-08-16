# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Outreach MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

# Requires the Amplify add-on on the org's Outreach plan.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="outreach",
#         display_name="Outreach",
#         description="Prospects, sequences, accounts, opportunities, and meetings in Outreach.",
#         icon="/icons/connectors/outreach.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://api.outreach.io/mcp",
#         # Confirmed live via https://api.outreach.io/.well-known/oauth-protected-resource/mcp
#         # -> AS https://api.outreach.io, whose oauth-authorization-server metadata advertises
#         # a `registration_endpoint` (RFC 7591 DCR) at the dedicated `/mcpOAuth/*` paths (distinct
#         # from Outreach's direct API OAuth endpoints). These are only the UI-hint defaults; the
#         # authorize flow always re-discovers live (see dcr.py).
#         authorization_url="https://api.outreach.io/mcpOAuth/authorize",
#         token_url="https://api.outreach.io/mcpOAuth/token",
#         default_scopes=["prospects.all"],
#         supports_dcr=True,
#         documentation_url="https://developers.outreach.io/mcp-server",
#         auth_hint=AuthHint(
#             label="Outreach OAuth",
#             help_text="Requires the Amplify add-on. Sign in with your Outreach account; tool access inherits your RBAC profile.",
#         ),
#         tags=["sales", "sdr", "sales-engagement"],
#     )
# )
# class OutreachMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
