# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """DocuSign MCP server template — official remote server (Streamable HTTP), OAuth, no DCR.

# Open beta. `mcp.docusign.com` is the production endpoint; a demo/sandbox account should use
# `mcp-d.docusign.com/mcp` instead.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="docusign",
#         display_name="DocuSign",
#         description="Agreements, envelopes, and e-signature workflows via DocuSign IAM.",
#         icon="/icons/connectors/docusign.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://mcp.docusign.com/mcp",
#         # Confirmed live via https://mcp.docusign.com/.well-known/oauth-protected-resource/mcp
#         # -> AS https://account.docusign.com (DocuSign's standard account/auth server), whose
#         # oauth-authorization-server metadata has no `registration_endpoint` — DocuSign
#         # requires an Integration Key (a static OAuth app) created under Apps and Keys rather
#         # than dynamic client registration. These are only the UI-hint defaults; the authorize
#         # flow always re-discovers live (see dcr.py).
#         authorization_url="https://account.docusign.com/oauth/auth",
#         token_url="https://account.docusign.com/oauth/token",
#         default_scopes=["signature", "aow_manage", "adm_store_unified_repo_read"],
#         supports_dcr=False,
#         documentation_url="https://developers.docusign.com/platform/mcp-server/",
#         auth_hint=AuthHint(
#             label="DocuSign OAuth",
#             help_text=(
#                 "Create an Integration Key (App and Keys) in your DocuSign account and provide "
#                 "the client ID/secret below — DocuSign does not support dynamic client "
#                 "registration. Beta feature; use at your own discretion."
#             ),
#         ),
#         tags=["e-signature", "docs", "contracts"],
#     )
# )
# class DocuSignMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
