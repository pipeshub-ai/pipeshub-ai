# ruff: noqa

"""
Harvest API Usage Examples

This example demonstrates how to use the Harvest DataSource to interact with
the Harvest API v2, covering:
- Authentication (OAuth2, Personal Access Token)
- Initializing the Client and DataSource
- Fetching Current User and Company Info
- Listing Projects, Time Entries, Clients, Tasks
- Listing Invoices, Expenses, Roles

Prerequisites:
For OAuth2:
1. Create a Harvest OAuth2 app at https://id.getharvest.com/oauth2/access_tokens/new
2. Set HARVEST_CLIENT_ID and HARVEST_CLIENT_SECRET environment variables
3. Set HARVEST_ACCOUNT_ID environment variable
4. The OAuth flow will automatically open a browser for authorization

For Personal Access Token:
1. Go to https://id.getharvest.com/developers
2. Create a new personal access token
3. Set HARVEST_ACCESS_TOKEN and HARVEST_ACCOUNT_ID environment variables
"""

import asyncio
import json
import os

from app.sources.client.harvest.harvest import (
    HarvestClient,
    HarvestOAuthConfig,
    HarvestTokenConfig,
    HarvestResponse,
)
from app.sources.external.harvest.harvest import HarvestDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("HARVEST_CLIENT_ID")
CLIENT_SECRET = os.getenv("HARVEST_CLIENT_SECRET")

# Personal Access Token (second priority)
ACCESS_TOKEN = os.getenv("HARVEST_ACCESS_TOKEN")

# Account ID (required for all requests)
ACCOUNT_ID = os.getenv("HARVEST_ACCOUNT_ID")

# OAuth redirect URI
REDIRECT_URI = os.getenv("HARVEST_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: HarvestResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses (users, time_entries, projects, clients, tasks, invoices, expenses)
            for key in ("users", "time_entries", "projects", "clients",
                        "tasks", "invoices", "expenses", "roles",
                        "project_assignments"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
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
    print_section("Initializing Harvest Client")

    if not ACCOUNT_ID:
        print("  HARVEST_ACCOUNT_ID is required for all Harvest API requests.")
        print("  Please set the HARVEST_ACCOUNT_ID environment variable.")
        return

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Harvest OAuth authorization URL: https://id.getharvest.com/oauth2/authorize
            # Harvest token endpoint: https://id.getharvest.com/oauth2/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://id.getharvest.com/oauth2/authorize",
                token_endpoint="https://id.getharvest.com/oauth2/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],  # Harvest doesn't use scopes in auth URL
                scope_delimiter=" ",
                auth_method="body",  # Harvest sends credentials in POST body
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = HarvestOAuthConfig(
                access_token=access_token,
                account_id=ACCOUNT_ID,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Personal Access Token
    if config is None and ACCESS_TOKEN:
        print("  Using Personal Access Token authentication")
        config = HarvestTokenConfig(
            token=ACCESS_TOKEN,
            account_id=ACCOUNT_ID,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - HARVEST_CLIENT_ID and HARVEST_CLIENT_SECRET (for OAuth2)")
        print("   - HARVEST_ACCESS_TOKEN (for Personal Access Token)")
        print("   And always set HARVEST_ACCOUNT_ID")
        return

    client = HarvestClient.build_with_config(config)
    data_source = HarvestDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. Get Company Info
        print_section("Company Info")
        company_resp = await data_source.get_company()
        print_result("Get Company", company_resp)

        # 4. List Projects
        print_section("Projects")
        projects_resp = await data_source.list_projects()
        print_result("List Projects", projects_resp)

        # 5. List Time Entries
        print_section("Time Entries")
        time_entries_resp = await data_source.list_time_entries()
        print_result("List Time Entries", time_entries_resp)

        # 6. List Clients
        print_section("Clients")
        clients_resp = await data_source.list_clients()
        print_result("List Clients", clients_resp)

        # 7. List Tasks
        print_section("Tasks")
        tasks_resp = await data_source.list_tasks()
        print_result("List Tasks", tasks_resp)

        # 8. List Invoices
        print_section("Invoices")
        invoices_resp = await data_source.list_invoices()
        print_result("List Invoices", invoices_resp)

        # 9. List Expenses
        print_section("Expenses")
        expenses_resp = await data_source.list_expenses()
        print_result("List Expenses", expenses_resp)

        # 10. List Roles
        print_section("Roles")
        roles_resp = await data_source.list_roles()
        print_result("List Roles", roles_resp)

        # 11. List Project Assignments (current user)
        print_section("Project Assignments (Current User)")
        assignments_resp = await data_source.list_project_assignments()
        print_result("List Project Assignments", assignments_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Harvest API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
