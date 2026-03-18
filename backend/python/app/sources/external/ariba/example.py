# ruff: noqa

"""
SAP Ariba API Usage Examples

This example demonstrates how to use the Ariba DataSource to interact with
the SAP Ariba API, covering:
- Authentication (client_credentials OAuth2)
- Initializing the Client and DataSource
- Listing Sourcing Projects, Purchase Orders, Invoices
- Getting Requisitions, Suppliers, Contracts

Prerequisites:
1. Get your SAP Ariba API client_id and client_secret
2. Set ARIBA_CLIENT_ID and ARIBA_CLIENT_SECRET environment variables
3. Optionally set ARIBA_TOKEN_ENDPOINT and ARIBA_BASE_URL
"""

import asyncio
import json
import os

from app.sources.client.ariba.ariba import (
    AribaClient,
    AribaClientCredentialsConfig,
    AribaResponse,
)
from app.sources.external.ariba.ariba import AribaDataSource

# --- Configuration ---
CLIENT_ID = os.getenv("ARIBA_CLIENT_ID")
CLIENT_SECRET = os.getenv("ARIBA_CLIENT_SECRET")
TOKEN_ENDPOINT = os.getenv("ARIBA_TOKEN_ENDPOINT", "https://api.ariba.com/v2/oauth/token")
BASE_URL = os.getenv("ARIBA_BASE_URL", "https://openapi.ariba.com/api")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: AribaResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("sourcing_projects", "purchase_orders", "invoices",
                        "requisitions", "suppliers", "contracts", "results",
                        "items", "records"):
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
    print_section("Initializing SAP Ariba Client")

    if not CLIENT_ID or not CLIENT_SECRET:
        print("  No valid authentication method found.")
        print("   Please set the following:")
        print("   - ARIBA_CLIENT_ID")
        print("   - ARIBA_CLIENT_SECRET")
        return

    print("  Using client_credentials OAuth2 authentication")
    config = AribaClientCredentialsConfig(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_endpoint=TOKEN_ENDPOINT,
        base_url=BASE_URL,
    )

    client = AribaClient.build_with_config(config)
    data_source = AribaDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Sourcing Projects
        print_section("Sourcing Projects")
        projects_resp = await data_source.list_sourcing_projects(limit=10)
        print_result("List Sourcing Projects", projects_resp)

        # 3. List Purchase Orders
        print_section("Purchase Orders")
        orders_resp = await data_source.list_purchase_orders(limit=10)
        print_result("List Purchase Orders", orders_resp)

        # 4. List Invoices
        print_section("Invoices")
        invoices_resp = await data_source.list_invoices(limit=10)
        print_result("List Invoices", invoices_resp)

        # 5. List Requisitions
        print_section("Requisitions")
        reqs_resp = await data_source.list_requisitions(limit=10)
        print_result("List Requisitions", reqs_resp)

        # 6. List Suppliers
        print_section("Suppliers")
        suppliers_resp = await data_source.list_suppliers(limit=10)
        print_result("List Suppliers", suppliers_resp)

        # 7. List Contracts
        print_section("Contracts")
        contracts_resp = await data_source.list_contracts(limit=10)
        print_result("List Contracts", contracts_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All SAP Ariba API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
