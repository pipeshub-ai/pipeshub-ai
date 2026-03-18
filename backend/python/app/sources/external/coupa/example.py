# ruff: noqa

"""
Coupa API Usage Examples

This example demonstrates how to use the Coupa DataSource to interact with
the Coupa API, covering:
- Authentication (API Key, OAuth2 client_credentials)
- Initializing the Client and DataSource
- Listing Purchase Orders, Invoices, Requisitions
- Getting Suppliers, Contracts, Users, Departments
- Getting Expense Reports

Prerequisites:
For API Key:
1. Get your Coupa API key from Coupa admin
2. Set COUPA_API_KEY and COUPA_INSTANCE environment variables

For OAuth2:
1. Get your OAuth2 client_id and client_secret
2. Set COUPA_CLIENT_ID, COUPA_CLIENT_SECRET, and COUPA_INSTANCE environment variables
"""

import asyncio
import json
import os

from app.sources.client.coupa.coupa import (
    CoupaApiKeyConfig,
    CoupaClient,
    CoupaOAuthConfig,
    CoupaResponse,
)
from app.sources.external.coupa.coupa import CoupaDataSource

# --- Configuration ---
# API Key credentials
API_KEY = os.getenv("COUPA_API_KEY")

# OAuth2 credentials
CLIENT_ID = os.getenv("COUPA_CLIENT_ID")
CLIENT_SECRET = os.getenv("COUPA_CLIENT_SECRET")

# Instance name (required for both auth methods)
INSTANCE = os.getenv("COUPA_INSTANCE")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: CoupaResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("purchase_orders", "invoices", "requisitions",
                        "suppliers", "contracts", "users", "departments",
                        "expense_reports", "results", "items"):
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
    print_section("Initializing Coupa Client")

    if not INSTANCE:
        print("  COUPA_INSTANCE is required.")
        print("   Please set COUPA_INSTANCE environment variable.")
        return

    config = None

    # Priority 1: API Key
    if API_KEY:
        print("  Using API Key authentication")
        config = CoupaApiKeyConfig(api_key=API_KEY, instance=INSTANCE)

    # Priority 2: OAuth2
    if config is None and CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 client_credentials authentication")
        config = CoupaOAuthConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            instance=INSTANCE,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - COUPA_API_KEY (for API Key auth)")
        print("   - COUPA_CLIENT_ID and COUPA_CLIENT_SECRET (for OAuth2)")
        return

    client = CoupaClient.build_with_config(config)
    data_source = CoupaDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Purchase Orders
        print_section("Purchase Orders")
        orders_resp = await data_source.list_purchase_orders(limit=10)
        print_result("List Purchase Orders", orders_resp)

        # 3. List Invoices
        print_section("Invoices")
        invoices_resp = await data_source.list_invoices(limit=10)
        print_result("List Invoices", invoices_resp)

        # 4. List Requisitions
        print_section("Requisitions")
        reqs_resp = await data_source.list_requisitions(limit=10)
        print_result("List Requisitions", reqs_resp)

        # 5. List Suppliers
        print_section("Suppliers")
        suppliers_resp = await data_source.list_suppliers(limit=10)
        print_result("List Suppliers", suppliers_resp)

        # 6. List Contracts
        print_section("Contracts")
        contracts_resp = await data_source.list_contracts(limit=10)
        print_result("List Contracts", contracts_resp)

        # 7. List Users
        print_section("Users")
        users_resp = await data_source.list_users(limit=10)
        print_result("List Users", users_resp)

        # 8. List Departments
        print_section("Departments")
        depts_resp = await data_source.list_departments(limit=10)
        print_result("List Departments", depts_resp)

        # 9. List Expense Reports
        print_section("Expense Reports")
        expense_resp = await data_source.list_expense_reports(limit=10)
        print_result("List Expense Reports", expense_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Coupa API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
