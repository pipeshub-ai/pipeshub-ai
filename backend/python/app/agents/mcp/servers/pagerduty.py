"""PagerDuty MCP server template — official hosted server (Streamable HTTP), OAuth, no DCR.

This replaces the local ``pagerduty-mcp`` STDIO package: PagerDuty deprecated that server and
archived its GitHub repository, and points everyone at the hosted one.

One URL for everyone: ``https://mcp.pagerduty.com/mcp``. An EU-region account cannot use this
card, because the server address differs per region (``https://mcp.eu.pagerduty.com/mcp``) and
a catalog instance always uses the template's ``default_url``; an EU admin adds a custom server
at their region's URL instead, and sign-in discovers the same authorization server either way.
"""

from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="pagerduty",
        display_name="PagerDuty",
        description="Incidents, services, on-call schedules, and event orchestrations in PagerDuty.",
        icon="/icons/connectors/pagerduty.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.pagerduty.com/mcp",
        # Confirmed live 2026-11-09 via
        # discover_oauth_metadata("https://mcp.pagerduty.com/mcp"): the 401 names
        # https://mcp.pagerduty.com/.well-known/oauth-protected-resource/mcp -> resource
        # https://mcp.pagerduty.com/mcp, authorization server
        # https://app.pagerduty.com/global/oauth/anonymous. That server's RFC 8414 metadata
        # (.../.well-known/oauth-authorization-server/global/oauth/anonymous) lists no
        # `registration_endpoint`, and PagerDuty's docs say it does not support dynamic client
        # registration, so the admin creates a Scoped OAuth app and pastes its client
        # ID/secret. Its OpenID document does advertise a registration endpoint, but discovery
        # reads the RFC 8414 one first, so PipesHub sees no DCR. PKCE S256 is supported. These
        # are only the UI-hint defaults; the authorize flow always re-discovers live (see dcr.py).
        authorization_url="https://app.pagerduty.com/global/oauth/authorize",
        token_url="https://app.pagerduty.com/global/oauth/token",
        # The server's protected-resource metadata publishes 84 scopes, every write scope among
        # them, so leaving this empty would ask the person signing in to grant all of them.
        # These are the read scopes behind the server's browse_* tools; a Scoped OAuth app must
        # be configured with at least these, since a request has to be a subset of the app's
        # scopes. `browse_activity` reads log entries, which PagerDuty publishes no scope for,
        # and the manage_* tools need write scopes: add either set here if a tool is refused.
        default_scopes=[
            "change_events.read",
            "escalation_policies.read",
            "event_orchestrations.read",
            "incident_workflows.read",
            "incidents.read",
            "oncalls.read",
            "schedules.read",
            "services.read",
            "status_pages.read",
            "teams.read",
            "users.read",
        ],
        supports_dcr=False,
        documentation_url="https://docs.pagerduty.com/ai-automation/advance/pagerduty-mcp-server",
        auth_hint=AuthHint(
            label="PagerDuty OAuth",
            help_text=(
                "Create a Scoped OAuth app in PagerDuty with the redirect URI shown below and "
                "the read scopes listed in this form, then paste its client ID and secret — "
                "PagerDuty does not support dynamic client registration."
            ),
        ),
        tags=["incident-management", "observability", "on-call"],
    )
)
class PagerDutyMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
