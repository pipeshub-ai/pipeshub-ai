# ruff: noqa

"""
HubSpot CRM SDK Usage Examples

This example demonstrates how to use the HubSpot DataSource (backed by the
official ``hubspot-api-client`` SDK) covering:
- Authentication (OAuth2, Private App Token)
- Initializing the Client and DataSource
- Listing Contacts, Companies, Deals
- Listing Owners

Prerequisites:
For OAuth2:
1. Create a HubSpot app at https://developers.hubspot.com/
2. Set HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Private App Token:
1. In HubSpot, go to Settings > Integrations > Private Apps
2. Create a Private App with appropriate scopes
3. Set HUBSPOT_ACCESS_TOKEN environment variable

OAuth Scopes:
- crm.objects.contacts.read
- crm.objects.companies.read
- crm.objects.deals.read
"""

import asyncio
import json
import os
from typing import Any

from app.sources.client.hubspot.hubspot import (
    HubSpotClient,
    HubSpotOAuthConfig,
    HubSpotResponse,
    HubSpotTokenConfig,
)
from app.sources.external.hubspot.hubspot import HubSpotDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("HUBSPOT_CLIENT_ID")
CLIENT_SECRET = os.getenv("HUBSPOT_CLIENT_SECRET")

# Private App Token (second priority)
ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("HUBSPOT_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str) -> None:
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: HubSpotResponse, show_data: bool = True) -> None:
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data: Any = response.data
            # Handle paginated list responses (SDK returns dicts with 'results' key)
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
                print(f"   Found {len(results)} results.")
                if results:
                    print(f"   Sample: {json.dumps(results[0], indent=2, default=str)[:400]}...")
                paging = data.get("paging")
                if paging:
                    print(f"   Paging: {json.dumps(paging, indent=2, default=str)[:200]}")
            else:
                # Generic response
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing HubSpot Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # HubSpot OAuth authorization URL:
            #   https://app.hubspot.com/oauth/authorize
            # HubSpot token endpoint:
            #   https://api.hubapi.com/oauth/v1/token
            # HubSpot uses "body" auth method (client_id/secret in POST body)
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.hubspot.com/oauth/authorize",
                token_endpoint="https://api.hubapi.com/oauth/v1/token",
                redirect_uri=REDIRECT_URI,
                scopes=[
                    "crm.objects.contacts.read",
                    "crm.objects.companies.read",
                    "crm.objects.deals.read",
                ],
                scope_delimiter=" ",
                auth_method="body",  # HubSpot sends credentials in POST body
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = HubSpotOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Private App Token
    if config is None and ACCESS_TOKEN:
        print("  Using Private App Token authentication")
        config = HubSpotTokenConfig(
            token=ACCESS_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET (for OAuth2)")
        print("   - HUBSPOT_ACCESS_TOKEN (for Private App Token)")
        return

    client = HubSpotClient.build_with_config(config)
    data_source = HubSpotDataSource(client)
    print("Client initialized successfully.")

    # 2. List Contacts
    print_section("Contacts")
    contacts_resp = data_source.list_contacts(limit=10)
    print_result("List Contacts", contacts_resp)

    # 3. List Companies
    print_section("Companies")
    companies_resp = data_source.list_companies(limit=10)
    print_result("List Companies", companies_resp)

    # 4. List Deals
    print_section("Deals")
    deals_resp = data_source.list_deals(limit=10)
    print_result("List Deals", deals_resp)

    # 5. List Owners
    print_section("Owners")
    owners_resp = data_source.list_owners(limit=10)
    print_result("List Owners", owners_resp)

    # 6. Get a specific contact if available
    if contacts_resp.success and contacts_resp.data:
        results = contacts_resp.data.get("results", [])
        if results:
            contact_id = str(results[0].get("id"))
            print_section(f"Contact Details: {contact_id}")
            contact_resp = data_source.get_contact(
                contact_id=contact_id,
                properties=["email", "firstname", "lastname", "phone", "company"],
            )
            print_result("Get Contact", contact_resp)

    # 7. List Deal Pipelines
    print_section("Deal Pipelines")
    pipelines_resp = data_source.list_pipelines(object_type="deals")
    print_result("List Deal Pipelines", pipelines_resp)

    # 8. List Contact Properties
    print_section("Contact Properties")
    props_resp = data_source.list_properties(object_type="contacts")
    print_result("List Contact Properties", props_resp)

    print("\n" + "=" * 80)
    print("  All HubSpot API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
