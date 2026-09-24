# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """PagerDuty MCP server template — official server, run over STDIO with a user API token."""
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="pagerduty",
#         display_name="PagerDuty",
#         description="Incidents, services, on-call schedules, and event orchestrations in PagerDuty.",
#         icon="/icons/connectors/pagerduty.svg",
#         transport=MCPTransport.STDIO,
#         default_auth_mode=MCPAuthMode.API_TOKEN,
#         supported_auth_modes=[MCPAuthMode.API_TOKEN],
#         command="uvx",
#         args=["pagerduty-mcp"],
#         required_env=["PAGERDUTY_USER_API_KEY"],
#         # Defaults to the US API host; EU accounts must override with
#         # https://api.eu.pagerduty.com.
#         optional_env=["PAGERDUTY_API_HOST"],
#         documentation_url="https://github.com/PagerDuty/pagerduty-mcp-server",
#         auth_hint=AuthHint(
#             label="PagerDuty User API Token",
#             help_text="Generate a User API Token from your PagerDuty profile icon > My Profile > User Settings.",
#         ),
#         tags=["incident-management", "observability", "on-call"],
#     )
# )
# class PagerDutyMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
