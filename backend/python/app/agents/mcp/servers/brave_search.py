# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Brave Search MCP server template — official server, run over STDIO."""
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="brave_search",
#         display_name="Brave Search",
#         description="Web and local search via the Brave Search API.",
#         icon="/icons/connectors/brave.svg",
#         transport=MCPTransport.STDIO,
#         default_auth_mode=MCPAuthMode.API_TOKEN,
#         supported_auth_modes=[MCPAuthMode.API_TOKEN],
#         command="npx",
#         # `@modelcontextprotocol/server-brave-search` lived in the now-archived
#         # `modelcontextprotocol/servers-archived` repo and is unmaintained. `@brave/...` is
#         # Brave's own officially maintained successor — pinned to an exact version (unlike
#         # an unpinned `npx -y <pkg>`) so a new upstream release can't silently change
#         # behavior (or break) under every existing instance.
#         args=["-y", "@brave/brave-search-mcp-server@2.0.85"],
#         required_env=["BRAVE_API_KEY"],
#         documentation_url="https://github.com/brave/brave-search-mcp-server",
#         auth_hint=AuthHint(
#             label="Brave Search API key",
#             placeholder="BSA...",
#             help_text="Create an API key at https://brave.com/search/api/",
#         ),
#         tags=["search", "web"],
#     )
# )
# class BraveSearchMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
