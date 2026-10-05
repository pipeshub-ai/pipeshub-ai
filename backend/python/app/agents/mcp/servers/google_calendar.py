"""Google Calendar MCP server template — official remote server (Streamable HTTP), OAuth-only.

Sibling of `google_drive.py` — see that module's docstring for why Google Workspace has one
MCP endpoint per product instead of a single combined one.
"""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="google_calendar",
        display_name="Google Calendar",
        description="Find availability and manage events on a Google Calendar.",
        icon="/icons/connectors/calendar.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://calendarmcp.googleapis.com/mcp/v1",
        # See google_drive.py: Google's direct OAuth endpoints, no DCR support — admin must
        # register a static OAuth client for this instance.
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        # Google documents `calendar.events.readonly` for this server, but the toolset also
        # creates, updates, deletes and responds to events, so events read/write is required.
        default_scopes=[
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
            "https://www.googleapis.com/auth/calendar.events.freebusy",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        # Google returns a refresh token only for an offline grant, and only re-issues
        # one when consent is re-shown; without both, every connection dies at expiry.
        authorization_params={"access_type": "offline", "prompt": "consent"},
        supports_dcr=False,
        documentation_url="https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server",
        auth_hint=AuthHint(
            label="Google OAuth",
            help_text=(
                "Enable both the Calendar API (calendar-json.googleapis.com) and the Calendar "
                "MCP API (calendarmcp.googleapis.com) on your Google Cloud project, then create "
                "an OAuth client ID and provide it below — Google does not support dynamic "
                "client registration."
            ),
        ),
        tags=["calendar", "productivity"],
    )
)
class GoogleCalendarMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
