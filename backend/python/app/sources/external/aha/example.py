# ruff: noqa

"""
Aha! API Usage Examples

This example demonstrates how to use the Aha! DataSource to interact with
the Aha! API (v1), covering:
- Authentication (OAuth2, API Key)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Products, Features, Ideas
- Working with Releases, Goals, Epics

Prerequisites:
For OAuth2:
1. Create an Aha! OAuth app at https://www.aha.io/api
2. Set AHA_CLIENT_ID and AHA_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Key:
1. Go to Settings > Account > API keys in your Aha! account
2. Set AHA_API_KEY environment variable

Subdomain:
Set AHA_SUBDOMAIN environment variable (e.g., 'yourcompany' for yourcompany.aha.io)

API Reference: https://www.aha.io/api
"""

import asyncio
import json
import os

from app.sources.client.aha.aha import (
    AhaClient,
    AhaOAuthConfig,
    AhaTokenConfig,
    AhaResponse,
)
from app.sources.external.aha.aha import AhaDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("AHA_CLIENT_ID")
CLIENT_SECRET = os.getenv("AHA_CLIENT_SECRET")

# API Key (second priority)
API_KEY = os.getenv("AHA_API_KEY")

# Subdomain (required)
SUBDOMAIN = os.getenv("AHA_SUBDOMAIN", "")

# OAuth redirect URI
REDIRECT_URI = os.getenv("AHA_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: AhaResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("products", "features", "ideas", "releases", "goals",
                        "epics", "users", "integrations"):
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
    print_section("Initializing Aha! Client")

    if not SUBDOMAIN:
        print("  AHA_SUBDOMAIN is required.")
        print("   Please set AHA_SUBDOMAIN environment variable (e.g., 'yourcompany').")
        return

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=f"https://{SUBDOMAIN}.aha.io/oauth/authorize",
                token_endpoint=f"https://{SUBDOMAIN}.aha.io/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = AhaOAuthConfig(
                subdomain=SUBDOMAIN,
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: API Key
    if config is None and API_KEY:
        print("  Using API Key authentication")
        config = AhaTokenConfig(subdomain=SUBDOMAIN, api_key=API_KEY)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - AHA_CLIENT_ID and AHA_CLIENT_SECRET (for OAuth2)")
        print("   - AHA_API_KEY (for API Key)")
        return

    client = AhaClient.build_with_config(config)
    data_source = AhaDataSource(client)
    print(f"Client initialized successfully (subdomain: {SUBDOMAIN}).")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Products
        print_section("Products")
        products_resp = await data_source.list_products(per_page=10)
        print_result("List Products", products_resp)

        # Extract first product ID for further exploration
        product_id = None
        if products_resp.success and products_resp.data:
            products = products_resp.data.get("products", [])
            if not products and isinstance(products_resp.data, list):
                products = products_resp.data
            if products:
                product_id = str(products[0].get("id") or products[0].get("product_id", ""))
                print(f"   Using Product: {products[0].get('name', 'Unknown')} (ID: {product_id})")

        if not product_id:
            print("   No products found. Skipping product-specific operations.")
            return

        # 4. Get Product Details
        print_section("Product Details")
        product_resp = await data_source.get_product(product_id=product_id)
        print_result("Get Product", product_resp)

        # 5. List Features
        print_section("Features")
        features_resp = await data_source.list_product_features(product_id=product_id, per_page=10)
        print_result("List Features", features_resp)

        # Get a specific feature if available
        if features_resp.success and features_resp.data:
            features = features_resp.data.get("features", [])
            if not features and isinstance(features_resp.data, list):
                features = features_resp.data
            if features:
                feature_id = str(features[0].get("id") or features[0].get("feature_id", ""))
                print_section("Feature Details")
                feature_resp = await data_source.get_feature(feature_id=feature_id)
                print_result("Get Feature", feature_resp)

        # 6. List Ideas
        print_section("Ideas")
        ideas_resp = await data_source.list_product_ideas(product_id=product_id, per_page=10)
        print_result("List Ideas", ideas_resp)

        # 7. List Releases
        print_section("Releases")
        releases_resp = await data_source.list_product_releases(product_id=product_id, per_page=10)
        print_result("List Releases", releases_resp)

        # Get a specific release if available
        if releases_resp.success and releases_resp.data:
            releases = releases_resp.data.get("releases", [])
            if not releases and isinstance(releases_resp.data, list):
                releases = releases_resp.data
            if releases:
                release_id = str(releases[0].get("id") or releases[0].get("release_id", ""))
                print_section("Release Details")
                release_resp = await data_source.get_release(release_id=release_id)
                print_result("Get Release", release_resp)

        # 8. List Goals
        print_section("Goals")
        goals_resp = await data_source.list_product_goals(product_id=product_id)
        print_result("List Goals", goals_resp)

        # 9. List Epics
        print_section("Epics")
        epics_resp = await data_source.list_product_epics(product_id=product_id, per_page=10)
        print_result("List Epics", epics_resp)

        # 10. List Users
        print_section("Users")
        users_resp = await data_source.list_users(per_page=10)
        print_result("List Users", users_resp)

        # 11. List Integrations
        print_section("Integrations")
        integrations_resp = await data_source.list_product_integrations(product_id=product_id)
        print_result("List Integrations", integrations_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Aha! API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
