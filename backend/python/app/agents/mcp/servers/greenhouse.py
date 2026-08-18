# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Greenhouse MCP server template — official remote server (Streamable HTTP), OAuth + DCR.

# `mcp.us.greenhouse.io` is the US-hosted default; Greenhouse also runs an EU region
# (`mcp.eu.greenhouse.io`) for EU-hosted accounts — an admin on that region should override
# `default_url` accordingly, everything else here still applies since the authorize flow
# always re-discovers live (see dcr.py). Open beta, available on Greenhouse Recruiting's
# Core/Plus/Pro tiers only; a Site Admin must enable MCP scopes in Dev Center first.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="greenhouse",
#         display_name="Greenhouse",
#         description="Jobs, candidates, applications, and hiring pipeline data in Greenhouse Recruiting.",
#         icon="/icons/connectors/greenhouse.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.us.greenhouse.io/mcp",
#         # Confirmed live via https://mcp.us.greenhouse.io/.well-known/oauth-protected-resource/mcp
#         # -> AS https://mcp.us.greenhouse.io, whose oauth-authorization-server metadata
#         # advertises a `registration_endpoint` (RFC 7591 DCR) alongside Greenhouse's own docs
#         # noting support for OAuth 2.0 with PKCE and DCR. These are only the UI-hint
#         # defaults; the authorize flow always re-discovers live (see dcr.py).
#         authorization_url="https://auth.greenhouse.io/authorize",
#         token_url="https://auth.greenhouse.io/token",
#         default_scopes=["greenhouse:mcp_client"],
#         supports_dcr=True,
#         documentation_url="https://support.greenhouse.io/hc/en-us/articles/52090956642075-Greenhouse-MCP",
#         auth_hint=AuthHint(
#             label="Greenhouse OAuth",
#             help_text=(
#                 "Requires a Core/Plus/Pro Greenhouse Recruiting plan. A Site Admin must first "
#                 "enable the required scopes under Dev Center > MCP Access, then sign in with a "
#                 "Greenhouse Recruiting account."
#             ),
#         ),
#         tags=["hr", "recruiting", "ats"],
#     )
# )
# class GreenhouseMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
