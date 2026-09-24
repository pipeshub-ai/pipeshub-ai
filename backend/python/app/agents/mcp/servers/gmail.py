# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Gmail MCP server template — official remote server (Streamable HTTP), OAuth-only.

# Sibling of `google_drive.py` — see that module's docstring for why Google Workspace has one
# MCP endpoint per product instead of a single combined one.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="gmail",
#         display_name="Gmail",
#         description="Search, read, and send email through a Gmail account.",
#         icon="/icons/connectors/gmail.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://gmailmcp.googleapis.com/mcp/v1",
#         # See google_drive.py: Google's direct OAuth endpoints, no DCR support — admin must
#         # register a static OAuth client for this instance.
#         authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
#         token_url="https://oauth2.googleapis.com/token",
#         default_scopes=[
#             "https://www.googleapis.com/auth/gmail.readonly",
#             "https://www.googleapis.com/auth/gmail.send",
#         ],
#         supports_dcr=False,
#         documentation_url="https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server",
#         auth_hint=AuthHint(
#             label="Google OAuth",
#             help_text=(
#                 "Enable the Gmail MCP API (gmailmcp.googleapis.com) on your Google Cloud project, "
#                 "then create an OAuth client ID and provide it below — Google does not support "
#                 "dynamic client registration."
#             ),
#         ),
#         tags=["email", "productivity"],
#     )
# )
# class GmailMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
