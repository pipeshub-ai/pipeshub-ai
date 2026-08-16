# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Dropbox MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

# Beta and currently read-only (browse/inspect/extract text) per Dropbox's own docs — there are
# no upload/create/delete tools yet.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="dropbox",
#         display_name="Dropbox",
#         description="Browse, search, and read files in a Dropbox account (beta, read-only).",
#         icon="/icons/connectors/dropbox.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.dropbox.com/mcp",
#         # Confirmed live via https://mcp.dropbox.com/.well-known/oauth-protected-resource/mcp
#         # -> AS https://www.dropbox.com, whose oauth-authorization-server metadata advertises a
#         # `registration_endpoint` (RFC 7591 DCR) — Dropbox's docs describe DCR as limited to a
#         # trusted client allowlist today, so a self-registered client may still need an admin
#         # to fall back to a manually-created Dropbox app. These are only the UI-hint defaults;
#         # the authorize flow always re-discovers live (see dcr.py).
#         authorization_url="https://www.dropbox.com/oauth2/authorize",
#         token_url="https://api.dropboxapi.com/oauth2/token",
#         default_scopes=["account_info.read", "files.metadata.read", "files.content.read"],
#         supports_dcr=True,
#         documentation_url="https://help.dropbox.com/integrations/connect-dropbox-mcp-server",
#         auth_hint=AuthHint(
#             label="Dropbox OAuth",
#             help_text="Sign in with your Dropbox account.",
#         ),
#         tags=["storage", "docs"],
#     )
# )
# class DropboxMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
