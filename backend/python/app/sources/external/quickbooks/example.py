# ruff: noqa

"""
QuickBooks Online API Usage Examples

This example demonstrates how to use the QuickBooks DataSource to interact with
the QuickBooks Online API v3, covering:
- Authentication (OAuth2)
- Initializing the Client and DataSource
- SQL-like query endpoint
- Fetching Customers, Invoices, Payments
- Company info

Prerequisites:
1. Create a QuickBooks app at https://developer.intuit.com
2. Set QUICKBOOKS_CLIENT_ID and QUICKBOOKS_CLIENT_SECRET
3. Complete OAuth flow to get access_token
4. Set QUICKBOOKS_ACCESS_TOKEN and QUICKBOOKS_COMPANY_ID

OAuth Endpoints:
- Auth: https://appcenter.intuit.com/connect/oauth2
- Token: https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
- Auth Method: "body"
"""

import asyncio
import json
import os

from app.sources.client.quickbooks.quickbooks import (
    QuickBooksClient,
    QuickBooksOAuthConfig,
    QuickBooksResponse,
)
from app.sources.external.quickbooks.quickbooks import QuickBooksDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
CLIENT_ID = os.getenv("QUICKBOOKS_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("QUICKBOOKS_CLIENT_SECRET", "")
ACCESS_TOKEN = os.getenv("QUICKBOOKS_ACCESS_TOKEN", "")
COMPANY_ID = os.getenv("QUICKBOOKS_COMPANY_ID", "")
REDIRECT_URI = os.getenv("QUICKBOOKS_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: QuickBooksResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing QuickBooks Client")

    access_token = ACCESS_TOKEN

    # Try OAuth flow if no access token
    if not access_token and CLIENT_ID and CLIENT_SECRET:
        print("  Starting OAuth flow...")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://appcenter.intuit.com/connect/oauth2",
                token_endpoint="https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
                redirect_uri=REDIRECT_URI,
                scopes=["com.intuit.quickbooks.accounting"],
                scope_delimiter=" ",
                auth_method="body",
            )
            access_token = token_response.get("access_token", "")
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")

    if not access_token or not COMPANY_ID:
        print("  No valid authentication found.")
        print("   Please set:")
        print("   - QUICKBOOKS_ACCESS_TOKEN and QUICKBOOKS_COMPANY_ID")
        print("   Or for OAuth flow:")
        print("   - QUICKBOOKS_CLIENT_ID, QUICKBOOKS_CLIENT_SECRET, QUICKBOOKS_COMPANY_ID")
        return

    config = QuickBooksOAuthConfig(
        access_token=access_token,
        company_id=COMPANY_ID,
        client_id=CLIENT_ID or None,
        client_secret=CLIENT_SECRET or None,
    )
    client = QuickBooksClient.build_with_config(config)
    data_source = QuickBooksDataSource(client)
    print(f"Client initialized for company: {COMPANY_ID}")

    try:
        # 2. Get Company Info
        print_section("Company Info")
        company_resp = await data_source.get_company_info(COMPANY_ID)
        print_result("Get Company Info", company_resp)

        # 3. Query Customers
        print_section("Query Customers")
        customers_resp = await data_source.query("SELECT * FROM Customer MAXRESULTS 5")
        print_result("Query Customers", customers_resp)

        # 4. Get a Specific Customer (ID 1)
        print_section("Customer Details")
        customer_resp = await data_source.get_customer("1")
        print_result("Get Customer", customer_resp)

        # 5. Query Invoices
        print_section("Query Invoices")
        invoices_resp = await data_source.query("SELECT * FROM Invoice MAXRESULTS 5")
        print_result("Query Invoices", invoices_resp)

        # 6. Query Items
        print_section("Query Items")
        items_resp = await data_source.query("SELECT * FROM Item MAXRESULTS 5")
        print_result("Query Items", items_resp)

        # 7. Query Accounts
        print_section("Query Accounts")
        accounts_resp = await data_source.query("SELECT * FROM Account MAXRESULTS 5")
        print_result("Query Accounts", accounts_resp)

        # 8. Query Vendors
        print_section("Query Vendors")
        vendors_resp = await data_source.query("SELECT * FROM Vendor MAXRESULTS 5")
        print_result("Query Vendors", vendors_resp)

        # 9. Query Employees
        print_section("Query Employees")
        employees_resp = await data_source.query("SELECT * FROM Employee MAXRESULTS 5")
        print_result("Query Employees", employees_resp)

    finally:
        # Cleanup
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All QuickBooks Online API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
