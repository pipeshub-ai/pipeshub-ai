# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Zoom MCP server template — official remote server (Streamable HTTP), OAuth, no DCR."""
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="zoom",
#         display_name="Zoom",
#         description="Meetings, recordings, chat, and calendar in Zoom Workplace.",
#         icon="/icons/connectors/zoom.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.zoom.us/mcp/zoom/streamable",
#         # Zoom's own docs are explicit that its MCP servers only support manual OAuth client
#         # registration (a General App created in the Zoom App Marketplace) — no RFC 7591 DCR
#         # and no CIMD. `zoom.us/oauth/*` are Zoom's standard OAuth 2.0 endpoints; these are
#         # only the UI-hint defaults, the authorize flow always re-discovers live (see dcr.py).
#         authorization_url="https://zoom.us/oauth/authorize",
#         token_url="https://zoom.us/oauth/token",
#         default_scopes=["meeting:read:search", "cloud_recording:read:list_user_recordings"],
#         supports_dcr=False,
#         documentation_url="https://developers.zoom.us/docs/mcp/servers/connect-to-zoom-mcp-servers/",
#         auth_hint=AuthHint(
#             label="Zoom OAuth",
#             help_text=(
#                 "Create a General App in the Zoom App Marketplace and provide its client "
#                 "ID/secret below — Zoom does not support dynamic client registration."
#             ),
#         ),
#         tags=["meetings", "collaboration"],
#     )
# )
# class ZoomMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
