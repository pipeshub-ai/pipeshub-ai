# ruff: noqa

"""
Coda API Usage Examples

This example demonstrates how to use the Coda DataSource to interact with
the Coda REST API, covering:
- Authentication (OAuth2 or API Token)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Docs and Folders
- Listing Tables, Rows, and Columns
- Listing Pages, Formulas, and Controls

Prerequisites:
For OAuth2:
1. Register your application at https://coda.io/account#apiSettings
2. Set CODA_CLIENT_ID and CODA_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Token:
1. Generate a token at https://coda.io/account#apiSettings
2. Set CODA_API_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.coda.coda import (
    CodaClient,
    CodaOAuthConfig,
    CodaResponse,
    CodaTokenConfig,
)
from app.sources.external.coda.coda import CodaDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("CODA_CLIENT_ID")
CLIENT_SECRET = os.getenv("CODA_CLIENT_SECRET")

# API Token (second priority)
API_TOKEN = os.getenv("CODA_API_TOKEN")

REDIRECT_URI = os.getenv("CODA_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: CodaResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict) and "items" in data:
                items = data["items"]
                print(f"   Found {len(items)} item(s)")
                if items:
                    print(f"   Sample: {json.dumps(items[0], indent=2, default=str)[:300]}...")
            else:
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Coda Client")

    # Determine authentication method (priority: OAuth > API Token)
    config = None

    if CLIENT_ID and CLIENT_SECRET:
        # OAuth2 authentication (highest priority)
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://coda.io/d/authorize",
                token_endpoint="https://coda.io/d/token",
                redirect_uri=REDIRECT_URI,
                scopes=["read", "write"],
                scope_delimiter=" ",
                auth_method="header",
            )

            # Extract access token from response
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = CodaOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")
            # Continue to check other auth methods

    if config is None and API_TOKEN:
        # API Token authentication (second priority)
        print("  Using API Token authentication")
        config = CodaTokenConfig(token=API_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - CODA_CLIENT_ID and CODA_CLIENT_SECRET (for OAuth2)")
        print("   - CODA_API_TOKEN (for API Token)")
        return

    client = CodaClient.build_with_config(config)
    data_source = CodaDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User (whoami)")
        user_resp = await data_source.whoami()
        print_result("Get User", user_resp)

        # 3. List Docs
        print_section("Docs")
        docs_resp = await data_source.list_docs(limit=5)
        print_result("List Docs", docs_resp)

        # Get first doc ID for further operations
        doc_id = None
        if docs_resp.success and docs_resp.data:
            items = docs_resp.data.get("items", []) if isinstance(docs_resp.data, dict) else []
            if items:
                doc_id = items[0].get("id")
                print(f"   Using doc ID: {doc_id}")

        if doc_id:
            # 4. Get Doc Details
            print_section(f"Doc Details ({doc_id})")
            doc_resp = await data_source.get_doc(doc_id=doc_id)
            print_result("Get Doc", doc_resp)

            # 5. List Pages
            print_section(f"Pages in Doc ({doc_id})")
            pages_resp = await data_source.list_pages(doc_id=doc_id, limit=5)
            print_result("List Pages", pages_resp)

            # 6. List Tables
            print_section(f"Tables in Doc ({doc_id})")
            tables_resp = await data_source.list_tables(doc_id=doc_id, limit=5)
            print_result("List Tables", tables_resp)

            # Get first table for row/column listing
            table_id = None
            if tables_resp.success and tables_resp.data:
                table_items = tables_resp.data.get("items", []) if isinstance(tables_resp.data, dict) else []
                if table_items:
                    table_id = table_items[0].get("id")
                    print(f"   Using table ID: {table_id}")

            if table_id:
                # 7. List Columns
                print_section(f"Columns in Table ({table_id})")
                cols_resp = await data_source.list_columns(
                    doc_id=doc_id, table_id_or_name=table_id, limit=5
                )
                print_result("List Columns", cols_resp)

                # 8. List Rows
                print_section(f"Rows in Table ({table_id})")
                rows_resp = await data_source.list_rows(
                    doc_id=doc_id, table_id_or_name=table_id, limit=5,
                    use_column_names=True
                )
                print_result("List Rows", rows_resp)

            # 9. List Formulas
            print_section(f"Formulas in Doc ({doc_id})")
            formulas_resp = await data_source.list_formulas(doc_id=doc_id, limit=5)
            print_result("List Formulas", formulas_resp)

            # 10. List Controls
            print_section(f"Controls in Doc ({doc_id})")
            controls_resp = await data_source.list_controls(doc_id=doc_id, limit=5)
            print_result("List Controls", controls_resp)

        # 11. List Folders
        print_section("Folders")
        folders_resp = await data_source.list_folders(limit=5)
        print_result("List Folders", folders_resp)

        # 12. List Categories
        print_section("Categories")
        categories_resp = await data_source.list_categories()
        print_result("List Categories", categories_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All operations completed!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
