# ruff: noqa

"""
NICE CXone API Usage Examples

This example demonstrates how to use the NICE CXone DataSource to interact with
the NICE CXone API, covering:
- Authentication (OAuth2 client_credentials, Bearer Token)
- Initializing the Client and DataSource
- Listing Agents and Agent States
- Listing Active Contacts
- Listing Skills, Teams, Campaigns
- Contact History Reporting

Prerequisites:
For OAuth2 (client_credentials):
1. Register an application in the NICE CXone admin panel
2. Set NICECXONE_CLIENT_ID and NICECXONE_CLIENT_SECRET environment variables
3. Set NICECXONE_AUTH_DOMAIN (e.g., cxone.niceincontact.com)
4. Set NICECXONE_CLUSTER (e.g., c1, c2, etc.)

For Bearer Token:
1. Set NICECXONE_ACCESS_TOKEN environment variable with your access token
2. Set NICECXONE_CLUSTER (e.g., c1, c2, etc.)
"""

import asyncio
import json
import os

from app.sources.client.nicecxone.nicecxone import (
    NiceCXoneClient,
    NiceCXoneOAuthConfig,
    NiceCXoneTokenConfig,
    NiceCXoneResponse,
)
from app.sources.external.nicecxone.nicecxone import NiceCXoneDataSource

# --- Configuration ---
# OAuth2 credentials
CLIENT_ID = os.getenv("NICECXONE_CLIENT_ID")
CLIENT_SECRET = os.getenv("NICECXONE_CLIENT_SECRET")
AUTH_DOMAIN = os.getenv("NICECXONE_AUTH_DOMAIN", "cxone.niceincontact.com")

# Bearer Token
ACCESS_TOKEN = os.getenv("NICECXONE_ACCESS_TOKEN")

# Cluster
CLUSTER = os.getenv("NICECXONE_CLUSTER", "c1")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: NiceCXoneResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("agents", "contacts", "skills", "teams", "campaigns",
                        "evaluations", "contactHistory", "dialingRules"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing NICE CXone Client")

    config = None

    # Priority 1: OAuth2 (client_credentials)
    if CLIENT_ID and CLIENT_SECRET:
        print(f"  Using OAuth2 (client_credentials) authentication (cluster: {CLUSTER})")
        config = NiceCXoneOAuthConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            auth_domain=AUTH_DOMAIN,
            cluster=CLUSTER,
        )

    # Priority 2: Bearer Token
    if config is None and ACCESS_TOKEN:
        print(f"  Using Bearer Token authentication (cluster: {CLUSTER})")
        config = NiceCXoneTokenConfig(
            token=ACCESS_TOKEN,
            cluster=CLUSTER,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - NICECXONE_CLIENT_ID and NICECXONE_CLIENT_SECRET (for OAuth2)")
        print("   - NICECXONE_ACCESS_TOKEN (for Bearer Token)")
        return

    client = NiceCXoneClient.build_with_config(config)

    # Ensure authentication for OAuth clients
    inner_client = client.get_client()
    if hasattr(inner_client, "ensure_authenticated"):
        print("  Authenticating via OAuth...")
        await inner_client.ensure_authenticated()
        print("  Authentication successful.")

    data_source = NiceCXoneDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Agents
        print_section("Agents")
        agents_resp = await data_source.get_agents(top=5)
        print_result("Get Agents", agents_resp)

        # 3. Get Agent States
        print_section("Agent States")
        states_resp = await data_source.get_agent_states()
        print_result("Get Agent States", states_resp)

        # 4. Get Active Contacts
        print_section("Active Contacts")
        contacts_resp = await data_source.get_active_contacts(top=5)
        print_result("Get Active Contacts", contacts_resp)

        # 5. Get Skills
        print_section("Skills")
        skills_resp = await data_source.get_skills(top=5)
        print_result("Get Skills", skills_resp)

        # 6. Get Teams
        print_section("Teams")
        teams_resp = await data_source.get_teams(top=5)
        print_result("Get Teams", teams_resp)

        # 7. Get Campaigns
        print_section("Campaigns")
        campaigns_resp = await data_source.get_campaigns(top=5)
        print_result("Get Campaigns", campaigns_resp)

        # 8. Get Contact History (last 7 days)
        print_section("Contact History")
        from datetime import datetime, timedelta
        end_date = datetime.utcnow().isoformat() + "Z"
        start_date = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        history_resp = await data_source.get_contact_history(
            start_date=start_date,
            end_date=end_date,
            top=5,
        )
        print_result("Get Contact History", history_resp)

        # 9. Get Dialing Rules
        print_section("Dialing Rules")
        rules_resp = await data_source.get_dialing_rules()
        print_result("Get Dialing Rules", rules_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All NICE CXone API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
