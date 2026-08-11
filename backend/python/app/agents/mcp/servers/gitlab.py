# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """GitLab MCP server template — official server (Streamable HTTP), OAuth + DCR.

# GitLab.com is the default target; the same MCP server ships on GitLab Self-Managed and
# Dedicated at `https://<your-instance>/api/v4/mcp`, so an admin creating a custom instance can
# override `default_url` with their own domain and everything else here (auth endpoints,
# DCR) still applies since it's the same GitLab OAuth app per-instance, not a shared one.
# """
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="gitlab",
#         display_name="GitLab",
#         description="Projects, issues, merge requests, and pipelines on GitLab (beta).",
#         icon="/icons/connectors/gitlab.svg",
#         transport=MCPTransport.STREAMABLE_HTTP,
#         default_auth_mode=MCPAuthMode.OAUTH,
#         supported_auth_modes=[MCPAuthMode.OAUTH],
#         default_url="https://gitlab.com/api/v4/mcp",
#         # Confirmed live via https://gitlab.com/.well-known/oauth-protected-resource/api/v4/mcp
#         # -> AS https://gitlab.com, whose oauth-authorization-server metadata advertises a
#         # `registration_endpoint` (RFC 7591 DCR), matching GitLab's own docs. These are only
#         # the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py) —
#         # important here since a self-managed instance's real endpoints differ from gitlab.com's.
#         authorization_url="https://gitlab.com/oauth/authorize",
#         token_url="https://gitlab.com/oauth/token",
#         default_scopes=["mcp"],
#         supports_dcr=True,
#         documentation_url="https://docs.gitlab.com/user/model_context_protocol/mcp_server/",
#         auth_hint=AuthHint(
#             label="GitLab OAuth",
#             help_text="Sign in with your GitLab.com or self-managed GitLab account.",
#         ),
#         tags=["dev", "vcs", "issues"],
#     )
# )
# class GitLabMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
