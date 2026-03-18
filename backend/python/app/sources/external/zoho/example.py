# ruff: noqa
"""
Zoho CRM API Usage Examples

This example demonstrates how to use the Zoho CRM DataSource to interact
with the Zoho CRM API, covering:
- Authentication (OAuth with grant_token or refresh_token)
- Initializing the Client and DataSource
- Listing modules, users, roles, profiles
- CRUD operations on records
- Searching records
- Organization info

Prerequisites:
1. Create a Zoho CRM OAuth app at https://api-console.zoho.com/
2. Set environment variables:
   - ZOHO_CLIENT_ID: OAuth client ID
   - ZOHO_CLIENT_SECRET: OAuth client secret
   - ZOHO_GRANT_TOKEN: Grant token (for initial auth) OR
   - ZOHO_REFRESH_TOKEN: Refresh token (for subsequent auth)
   - ZOHO_DOMAIN: Data center domain (US, EU, IN, CN, AU, JP, CA) - defaults to US

API Reference: https://www.zoho.com/crm/developer/docs/api/v7/
"""

import json
import os

from app.sources.client.zoho.zoho import (
    ZohoClient,
    ZohoGrantTokenConfig,
    ZohoRefreshTokenConfig,
    ZohoResponse,
)
from app.sources.external.zoho.zoho import ZohoDataSource


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: ZohoResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            if isinstance(response.data, list):
                print(f"   Found {len(response.data)} items")
                if response.data:
                    print(f"   Sample: {str(response.data[0])[:400]}...")
            elif isinstance(response.data, dict):
                print(f"   Data: {json.dumps(response.data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    """Example usage of Zoho CRM API."""
    CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
    CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
    GRANT_TOKEN = os.getenv("ZOHO_GRANT_TOKEN")
    REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
    DOMAIN = os.getenv("ZOHO_DOMAIN", "US")

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Please set ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET environment variables")
        return

    if not GRANT_TOKEN and not REFRESH_TOKEN:
        print("Please set ZOHO_GRANT_TOKEN or ZOHO_REFRESH_TOKEN environment variable")
        return

    # Initialize Zoho CRM client
    print_section("Initializing Zoho CRM Client")
    config = None
    if REFRESH_TOKEN:
        print("  Using refresh token authentication")
        config = ZohoRefreshTokenConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            refresh_token=REFRESH_TOKEN,
            domain=DOMAIN,
        )
    elif GRANT_TOKEN:
        print("  Using grant token authentication")
        config = ZohoGrantTokenConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            grant_token=GRANT_TOKEN,
            domain=DOMAIN,
        )

    if config is None:
        print("  No valid authentication method found.")
        return

    client = ZohoClient.build_with_config(config)
    data_source = ZohoDataSource(client)
    print("  Client initialized successfully.")

    # 1. Organization info
    print_section("Organization Info")
    org_resp = data_source.get_organization()
    print_result("Get Organization", org_resp)

    # 2. List modules
    print_section("Modules")
    modules_resp = data_source.list_modules()
    print_result("List Modules", modules_resp)

    # 3. List users
    print_section("Users")
    users_resp = data_source.list_users()
    print_result("List Users", users_resp)

    # 4. List roles
    print_section("Roles")
    roles_resp = data_source.list_roles()
    print_result("List Roles", roles_resp)

    # 5. List profiles
    print_section("Profiles")
    profiles_resp = data_source.list_profiles()
    print_result("List Profiles", profiles_resp)

    # 6. List records from Leads module
    print_section("Leads Records")
    leads_resp = data_source.list_records("Leads", per_page=5)
    print_result("List Leads", leads_resp)

    # 7. Search records
    print_section("Search Leads")
    search_resp = data_source.search_records("Leads", word="test")
    print_result("Search Leads", search_resp)

    print("\n" + "=" * 80)
    print("  All Zoho CRM API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
