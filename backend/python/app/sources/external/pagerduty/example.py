# ruff: noqa

"""
PagerDuty API Usage Examples

This example demonstrates how to use the PagerDuty DataSource to interact with
the PagerDuty REST API v2, covering:
- Authentication (OAuth2, API Token)
- Initializing the Client and DataSource
- Listing Users and Current User
- Listing Services
- Listing Incidents
- Listing Teams

Prerequisites:
For OAuth2:
1. Register an OAuth application in PagerDuty (Account > App Registration)
2. Set PAGERDUTY_CLIENT_ID and PAGERDUTY_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Token:
1. Generate an API token in PagerDuty (User Settings > API Access)
2. Set PAGERDUTY_API_TOKEN environment variable

OAuth2 Details:
- Auth URL: https://identity.pagerduty.com/oauth/authorize
- Token URL: https://identity.pagerduty.com/oauth/token
- Auth method: "body" (client_id/client_secret in POST body)
- Scopes (space-delimited): "read write"
"""

import asyncio
import json
import os

from app.sources.client.pagerduty.pagerduty import (
    PagerDutyClient,
    PagerDutyOAuthConfig,
    PagerDutyResponse,
    PagerDutyTokenConfig,
)
from app.sources.external.pagerduty.pagerduty import PagerDutyDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("PAGERDUTY_CLIENT_ID")
CLIENT_SECRET = os.getenv("PAGERDUTY_CLIENT_SECRET")

# API Token (second priority)
API_TOKEN = os.getenv("PAGERDUTY_API_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv(
    "PAGERDUTY_REDIRECT_URI", "http://localhost:8080/callback"
)


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: PagerDutyResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(
                        f"   Sample: "
                        f"{json.dumps(data[0], indent=2, default=str)[:400]}..."
                    )
            elif isinstance(data, dict):
                print(
                    f"   Data: "
                    f"{json.dumps(data, indent=2, default=str)[:500]}..."
                )
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing PagerDuty Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # PagerDuty uses identity subdomain for OAuth
            # Auth method is "body" (client_id/secret in POST body)
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://identity.pagerduty.com/oauth/authorize",
                token_endpoint="https://identity.pagerduty.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=["read", "write"],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = PagerDutyOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: API Token
    if config is None and API_TOKEN:
        print("  Using API Token authentication")
        config = PagerDutyTokenConfig(token=API_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print(
            "   - PAGERDUTY_CLIENT_ID and PAGERDUTY_CLIENT_SECRET (for OAuth2)"
        )
        print("   - PAGERDUTY_API_TOKEN (for API Token)")
        return

    client = PagerDutyClient.build_with_config(config)
    data_source = PagerDutyDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = data_source.list_users()
        print_result("List Users", users_resp)

        # 3. Get Current User
        print_section("Current User")
        me_resp = data_source.get_current_user()
        print_result("Get Current User", me_resp)

        # 4. List Services
        print_section("Services")
        services_resp = data_source.list_services()
        print_result("List Services", services_resp)

        # 5. List Incidents
        print_section("Incidents")
        incidents_resp = data_source.list_incidents(
            params={"statuses[]": ["triggered", "acknowledged"]}
        )
        print_result("List Incidents", incidents_resp)

        # 6. List Teams
        print_section("Teams")
        teams_resp = data_source.list_teams()
        print_result("List Teams", teams_resp)

        # 7. List Escalation Policies
        print_section("Escalation Policies")
        ep_resp = data_source.list_escalation_policies()
        print_result("List Escalation Policies", ep_resp)

        # 8. List Schedules
        print_section("Schedules")
        sched_resp = data_source.list_schedules()
        print_result("List Schedules", sched_resp)

        # 9. List On-Calls
        print_section("On-Calls")
        oncall_resp = data_source.list_oncalls()
        print_result("List On-Calls", oncall_resp)

    finally:
        print("\nDone.")

    print("\n" + "=" * 80)
    print("  All PagerDuty API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
