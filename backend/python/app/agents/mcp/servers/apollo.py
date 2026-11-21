"""Apollo MCP server template — official hosted server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="apollo",
        display_name="Apollo",
        description="Search and enrich people and companies, and manage contacts, accounts and tasks in Apollo.",
        icon="/icons/connectors/apollo.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.apollo.io/mcp",
        # Confirmed live: an unauthenticated `initialize` on https://mcp.apollo.io/mcp returns 401 with
        # `resource_metadata=https://mcp.apollo.io/.well-known/oauth-protected-resource`
        # -> AS https://mcp.apollo.io, whose oauth-authorization-server metadata advertises a
        # `registration_endpoint` (RFC 7591 DCR), `code_challenge_methods_supported=["S256"]` and
        # `client_id_metadata_document_supported=true`. These are only the UI-hint defaults; the
        # authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://mcp.apollo.io/mcp/oauth_metadata/redirect_to_authorize",
        token_url="https://mcp.apollo.io/api/v1/oauth/token",
        # Apollo advertises one scope per API area (80+). Asking for all of them would show a huge
        # consent screen and let search/enrichment spend credits, so request a focused read-mostly
        # subset instead: profile, people/company search and enrichment, and reading CRM records.
        # An admin who needs more (sending email, campaigns, writes) gets a step-up sign-in
        # on `insufficient_scope` (see step_up.py).
        default_scopes=[
            "read_user_profile",
            "app_scopes",
            "mcp_scope",
            "mixed_people_api_search",
            "mixed_companies_search",
            "organizations_search",
            "organizations_enrich",
            "organizations_job_posting",
            "people_match",
            "contacts_search",
            "contact_read",
            "accounts_search",
            "tasks_list",
            "users_list",
        ],
        supports_dcr=True,
        documentation_url="https://docs.apollo.io/docs/apollo-mcp",
        auth_hint=AuthHint(
            label="Apollo OAuth",
            help_text="Sign in with your Apollo account. Search and enrichment tools can spend Apollo credits.",
        ),
        tags=["sales", "crm", "prospecting", "enrichment", "contacts"],
    )
)
class ApolloMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
