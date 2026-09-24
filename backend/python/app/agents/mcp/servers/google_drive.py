# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Google Drive MCP server template — official remote server (Streamable HTTP), OAuth-only.

# Google Workspace ships one dedicated MCP server per product rather than a single combined
# endpoint — this template covers Drive only; see `gmail.py` / `google_calendar.py` for the
# others. Each requires the matching `*mcp.googleapis.com` API enabled on the admin's Google
# Cloud project before the endpoint responds (`gcloud services enable drivemcp.googleapis.com`).
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="google_drive",
#         display_name="Google Drive",
#         description="Search, read, and manage files in a Google Drive account.",
#         icon="/icons/connectors/drive.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://drivemcp.googleapis.com/mcp/v1",
#         # Google's own OAuth 2.0 endpoints — the Drive MCP server is fronted by standard
#         # Google auth, not a dedicated AS the way Notion/Atlassian's MCP servers are, and
#         # Google does not support RFC 7591 open dynamic client registration: the admin must
#         # create an OAuth client ID in Google Cloud Console (see the docs link below) and
#         # register it as a static OAuth app on the instance rather than relying on DCR.
#         authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
#         token_url="https://oauth2.googleapis.com/token",
#         default_scopes=["https://www.googleapis.com/auth/drive"],
#         supports_dcr=False,
#         documentation_url="https://developers.google.com/workspace/drive/api/guides/configure-mcp-server",
#         auth_hint=AuthHint(
#             label="Google OAuth",
#             help_text=(
#                 "Enable the Drive MCP API (drivemcp.googleapis.com) on your Google Cloud project, "
#                 "then create an OAuth client ID and provide it below — Google does not support "
#                 "dynamic client registration."
#             ),
#         ),
#         tags=["storage", "docs", "productivity"],
#     )
# )
# class GoogleDriveMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
