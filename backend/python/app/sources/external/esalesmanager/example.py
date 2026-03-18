# ruff: noqa

"""
eSalesManager API Usage Examples

This example demonstrates how to use the eSalesManager DataSource to interact
with the eSalesManager API, covering:
- Authentication (API Key via X-API-Key header)
- Initializing the Client and DataSource
- Listing Customers, Contacts, Activities, Deals
- Fetching Products, Tasks, Reports, Users

Prerequisites:
1. Obtain an API key from eSalesManager
2. Set ESALESMANAGER_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.esalesmanager.esalesmanager import (
    ESalesManagerClient,
    ESalesManagerApiKeyConfig,
    ESalesManagerResponse,
)
from app.sources.external.esalesmanager.esalesmanager import ESalesManagerDataSource

# --- Configuration ---
API_KEY = os.getenv("ESALESMANAGER_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: ESalesManagerResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("customers", "contacts", "activities", "deals", "products",
                        "tasks", "reports", "users", "results"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
                return
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing eSalesManager Client")

    if not API_KEY:
        print("  No API key found.")
        print("   Please set ESALESMANAGER_API_KEY environment variable.")
        return

    print("  Using API Key authentication (X-API-Key header)")
    config = ESalesManagerApiKeyConfig(api_key=API_KEY)
    client = ESalesManagerClient.build_with_config(config)
    data_source = ESalesManagerDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Customers
        print_section("Customers")
        customers_resp = await data_source.list_customers(page=1, per_page=10)
        print_result("List Customers", customers_resp)

        # 3. List Contacts
        print_section("Contacts")
        contacts_resp = await data_source.list_contacts(page=1, per_page=10)
        print_result("List Contacts", contacts_resp)

        # 4. List Activities
        print_section("Activities")
        activities_resp = await data_source.list_activities(page=1, per_page=10)
        print_result("List Activities", activities_resp)

        # 5. List Deals
        print_section("Deals")
        deals_resp = await data_source.list_deals(page=1, per_page=10)
        print_result("List Deals", deals_resp)

        # 6. List Products
        print_section("Products")
        products_resp = await data_source.list_products(page=1, per_page=10)
        print_result("List Products", products_resp)

        # 7. List Tasks
        print_section("Tasks")
        tasks_resp = await data_source.list_tasks(page=1, per_page=10)
        print_result("List Tasks", tasks_resp)

        # 8. List Reports
        print_section("Reports")
        reports_resp = await data_source.list_reports(page=1, per_page=10)
        print_result("List Reports", reports_resp)

        # 9. List Users
        print_section("Users")
        users_resp = await data_source.list_users(page=1, per_page=10)
        print_result("List Users", users_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All eSalesManager API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
