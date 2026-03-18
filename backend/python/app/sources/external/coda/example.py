# ruff: noqa

"""
Coda API Usage Examples

This example demonstrates how to use the Coda DataSource to interact with
the Coda API, covering:
- Authentication (OAuth2, API Token)
- Initializing the Client and DataSource
- Getting current user info (whoami)
- Listing docs
- Listing categories

Prerequisites:
For OAuth2:
1. Create a Coda OAuth app at https://coda.io/account
2. Set CODA_CLIENT_ID and CODA_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Token:
1. Log in to Coda
2. Go to https://coda.io/account and generate an API token
3. Set CODA_API_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.coda.coda import (
    CodaClient,
    CodaOAuthConfig,
    CodaTokenConfig,
    CodaResponse,
)
from app.sources.external.coda.coda import CodaDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("CODA_CLIENT_ID")
CLIENT_SECRET = os.getenv("CODA_CLIENT_SECRET")

# API Token (second priority)
API_TOKEN = os.getenv("CODA_API_TOKEN")

# OAuth redirect URI
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
            # Handle list-type responses (items, docs, etc.)
            for key in ("items", "docs", "tables", "rows", "columns",
                        "pages", "formulas", "controls", "permissions"):
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
    print_section("Initializing Coda Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Coda OAuth authorization URL: https://coda.io/oauth2/authorize
            # Coda token endpoint: https://coda.io/oauth2/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://coda.io/oauth2/authorize",
                token_endpoint="https://coda.io/oauth2/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="header",  # Basic Auth with client_id:client_secret
            )

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

    # Priority 2: API Token
    if config is None and API_TOKEN:
        print("  Using API Token authentication")
        config = CodaTokenConfig(
            token=API_TOKEN,
        )

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
        # 2. Get Current User (whoami)
        print_section("Current User (whoami)")
        whoami_resp = await data_source.whoami()
        print_result("Whoami", whoami_resp)

        # 3. List Docs
        print_section("Docs")
        docs_resp = await data_source.list_docs()
        print_result("List Docs", docs_resp)

        # Extract first doc_id for further exploration
        doc_id = None
        if docs_resp.success and docs_resp.data:
            items = docs_resp.data.get("items", [])
            if items:
                doc_id = str(items[0].get("id"))
                print(f"   Using Doc: {items[0].get('name')} (ID: {doc_id})")

        if doc_id:
            # 4. Get Doc Details
            print_section("Doc Details")
            doc_resp = await data_source.get_doc(doc_id=doc_id)
            print_result("Get Doc", doc_resp)

            # 5. List Tables in Doc
            print_section("Tables")
            tables_resp = await data_source.list_tables(doc_id=doc_id)
            print_result("List Tables", tables_resp)

            # 6. List Pages in Doc
            print_section("Pages")
            pages_resp = await data_source.list_pages(doc_id=doc_id)
            print_result("List Pages", pages_resp)

            # 7. List Formulas in Doc
            print_section("Formulas")
            formulas_resp = await data_source.list_formulas(doc_id=doc_id)
            print_result("List Formulas", formulas_resp)

            # 8. List Permissions for Doc
            print_section("Permissions")
            perms_resp = await data_source.list_permissions(doc_id=doc_id)
            print_result("List Permissions", perms_resp)

        # 9. List Categories
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
    print("  All Coda API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
