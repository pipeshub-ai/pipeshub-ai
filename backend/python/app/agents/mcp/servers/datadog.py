# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Datadog MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

# `mcp.datadoghq.com` is the US1 default; other Datadog sites (US3, US5, EU1, AP1, AP2) use
# their own regional hosts (e.g. `mcp.datadoghq.eu`) — an admin on a non-US1 site should
# override `default_url` with the matching region, everything else here still applies since
# the authorize flow always re-discovers live (see dcr.py). "MCP Access" must be enabled in
# Organization Settings > Preferences before connecting.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="datadog",
#         display_name="Datadog",
#         description="Logs, metrics, traces, monitors, dashboards, and security signals in Datadog.",
#         icon="/icons/connectors/datadog.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.datadoghq.com/api/unstable/mcp-server/mcp",
#         # Confirmed live via
#         # https://mcp.datadoghq.com/.well-known/oauth-protected-resource/api/unstable/mcp-server/mcp
#         # -> AS https://mcp.datadoghq.com/v1/mcp, whose oauth-authorization-server metadata
#         # advertises a `registration_endpoint` (RFC 7591 DCR) at app.datadoghq.com. These are
#         # only the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
#         authorization_url="https://app.datadoghq.com/oauth2/v1/authorize",
#         token_url="https://app.datadoghq.com/api/v2/oauth2/token",
#         default_scopes=[],
#         supports_dcr=True,
#         documentation_url="https://docs.datadoghq.com/mcp_server/",
#         auth_hint=AuthHint(
#             label="Datadog OAuth",
#             help_text=(
#                 "Enable MCP Access under Organization Settings > Preferences, then sign in "
#                 "with your Datadog account. Use the endpoint matching your Datadog site if "
#                 "not on US1."
#             ),
#         ),
#         tags=["observability", "monitoring", "devops"],
#     )
# )
# class DatadogMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
