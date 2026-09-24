# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """PostHog MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="posthog",
#         display_name="PostHog",
#         description="Product analytics, feature flags, experiments, and error tracking in PostHog.",
#         icon="/icons/connectors/posthog.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.posthog.com/mcp",
#         # Confirmed live via https://mcp.posthog.com/.well-known/oauth-protected-resource/mcp
#         # -> AS https://oauth.posthog.com, whose oauth-authorization-server metadata advertises
#         # a `registration_endpoint` (RFC 7591 DCR). PostHog's AS advertises hundreds of
#         # granular per-feature scopes (feature_flag:read, insight:write, ...) rather than a
#         # small fixed set, so no default_scopes are sent — the authorize flow always
#         # re-discovers live and the AS's own default grant applies (see dcr.py).
#         authorization_url="https://oauth.posthog.com/oauth/authorize/",
#         token_url="https://oauth.posthog.com/oauth/token/",
#         default_scopes=[],
#         supports_dcr=True,
#         documentation_url="https://posthog.com/docs/model-context-protocol",
#         auth_hint=AuthHint(
#             label="PostHog OAuth",
#             help_text="Sign in with your PostHog account.",
#         ),
#         tags=["analytics", "product"],
#     )
# )
# class PostHogMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
