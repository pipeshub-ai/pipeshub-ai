# Disabled: temporarily removed from the MCP server catalog. Uncomment to re-enable.
# """Amazon Redshift MCP server template — official AWS Labs server, run over STDIO."""
# from app.agents.mcp.mcp_server_decorator import mcp_server
# from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


# @mcp_server(
#     MCPServerTemplate(
#         type_id="redshift",
#         display_name="Amazon Redshift",
#         description="Discover clusters/workgroups and run read-only SQL queries against Amazon Redshift.",
#         icon="/icons/connectors/redshift.svg",
#         transport=MCPTransport.STDIO,
#         default_auth_mode=MCPAuthMode.API_TOKEN,
#         supported_auth_modes=[MCPAuthMode.API_TOKEN],
#         command="uvx",
#         args=["awslabs.redshift-mcp-server@latest"],
#         # Static IAM credentials (rather than AWS_PROFILE, which needs a local profile file
#         # the server process wouldn't have) so the required set is a plain env-var allowlist.
#         required_env=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"],
#         documentation_url="https://awslabs.github.io/mcp/servers/redshift-mcp-server/",
#         auth_hint=AuthHint(
#             label="AWS credentials",
#             help_text="Provide an IAM access key with redshift:DescribeClusters and redshift-data:ExecuteStatement permissions.",
#         ),
#         tags=["database", "sql", "aws"],
#     )
# )
# class RedshiftMCPServer:
#     """Marker class — see `mcp_template` for the registered catalog entry."""
