# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Stripe MCP server template — official remote server (Streamable HTTP), OAuth + DCR,
# with a restricted-API-key fallback for clients that don't support OAuth.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="stripe",
#         display_name="Stripe",
#         description="Customers, payments, subscriptions, refunds, invoices, and balance data in Stripe.",
#         icon="/icons/connectors/stripe.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.API_TOKEN, MCPAuthMode.OAUTH],
#         default_url="https://mcp.stripe.com",
#         # Confirmed live via https://mcp.stripe.com/.well-known/oauth-protected-resource -> AS
#         # https://access.stripe.com/mcp, whose oauth-authorization-server metadata advertises a
#         # `registration_endpoint` (RFC 7591 DCR). These are only the UI-hint defaults; the
#         # authorize flow always re-discovers live (see dcr.py).
#         authorization_url="https://access.stripe.com/mcp/oauth2/authorize",
#         token_url="https://access.stripe.com/mcp/oauth2/token",
#         default_scopes=["mcp"],
#         supports_dcr=True,
#         documentation_url="https://docs.stripe.com/mcp",
#         auth_hint=AuthHint(
#             label="Restricted API key",
#             placeholder="rk_live_...",
#             help_text="Create a restricted key with least-privilege access at https://dashboard.stripe.com/apikeys, or sign in with OAuth.",
#         ),
#         tags=["payments", "billing", "finance"],
#     )
# )
# class StripeMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
