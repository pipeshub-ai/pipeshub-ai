# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Sentry MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="sentry",
#         display_name="Sentry",
#         description="Errors, stack traces, performance issues, and root-cause analysis in Sentry.",
#         icon="/icons/connectors/sentry.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.sentry.dev/mcp",
#         # Confirmed live via https://mcp.sentry.dev/.well-known/oauth-protected-resource/mcp ->
#         # AS https://mcp.sentry.dev, whose oauth-authorization-server metadata advertises a
#         # `registration_endpoint` (RFC 7591 DCR) and `client_id_metadata_document_supported`.
#         # These are only the UI-hint defaults; the authorize flow always re-discovers live
#         # (see dcr.py). Self-hosted Sentry uses a local STDIO server instead — not covered here.
#         authorization_url="https://mcp.sentry.dev/oauth/authorize",
#         token_url="https://mcp.sentry.dev/oauth/token",
#         default_scopes=["org:read", "project:write", "team:write", "event:write"],
#         supports_dcr=True,
#         documentation_url="https://mcp.sentry.dev/",
#         auth_hint=AuthHint(
#             label="Sentry OAuth",
#             help_text="Sign in with your Sentry account. Works on all plans, including free.",
#         ),
#         tags=["observability", "monitoring", "dev"],
#     )
# )
# class SentryMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
