# ruff: noqa

"""
Bynder API Usage Examples

This example demonstrates how to use the Bynder DataSource to interact with
the Bynder API via the official bynder-sdk, covering:
- Authentication (Permanent Token, OAuth2)
- Initializing the Client and DataSource
- Listing Media Assets, Collections, Tags
- Fetching Metaproperties, Brands, Account Users

Prerequisites:
For Permanent Token:
1. Generate a permanent token in Bynder portal settings
2. Set BYNDER_PERMANENT_TOKEN and BYNDER_DOMAIN environment variables

For OAuth:
1. Register an OAuth2 application in Bynder portal settings
2. Set BYNDER_CLIENT_ID, BYNDER_CLIENT_SECRET, BYNDER_DOMAIN, and
   BYNDER_REDIRECT_URI environment variables. Token must include access_token.

The BYNDER_DOMAIN is the full portal domain (e.g., "portal.getbynder.com").
"""

import json
import os

from app.sources.client.bynder.bynder import (
    BynderClient,
    BynderPermanentTokenConfig,
    BynderResponse,
)
from app.sources.external.bynder.bynder import BynderDataSource

# --- Configuration ---
PERMANENT_TOKEN = os.getenv("BYNDER_PERMANENT_TOKEN")
DOMAIN = os.getenv("BYNDER_DOMAIN", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: BynderResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2, default=str)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
            else:
                print(f"   Data: {str(data)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    if not DOMAIN:
        print("  BYNDER_DOMAIN environment variable is required.")
        print("   Example: export BYNDER_DOMAIN=portal.getbynder.com")
        return

    # 1. Initialize Client
    print_section("Initializing Bynder Client")

    if not PERMANENT_TOKEN:
        print("  No valid authentication method found.")
        print("   Please set BYNDER_PERMANENT_TOKEN")
        return

    print("  Using Permanent Token authentication")
    config = BynderPermanentTokenConfig(
        domain=DOMAIN,
        permanent_token=PERMANENT_TOKEN,
    )

    client = BynderClient.build_with_config(config)
    data_source = BynderDataSource(client)
    print(f"  Client initialized successfully (domain: {DOMAIN})")

    # 2. Get Media
    print_section("Media Assets")
    media_resp = data_source.get_media_list(limit=10)
    print_result("Get Media", media_resp)

    media_id = None
    if media_resp.success and media_resp.data:
        data = media_resp.data
        items = data if isinstance(data, list) else []
        if items:
            media_id = str(items[0].get("id")) if isinstance(items[0], dict) else None
            if media_id:
                print(f"   Using Media ID: {media_id}")

    if media_id:
        print_section("Media Details")
        media_detail_resp = data_source.get_media(media_id)
        print_result("Get Media Detail", media_detail_resp)

        print_section("Media Download URL")
        download_resp = data_source.get_media_download_url(media_id)
        print_result("Get Download URL", download_resp)

    # 3. Get Collections
    print_section("Collections")
    collections_resp = data_source.get_collections(limit=10)
    print_result("Get Collections", collections_resp)

    # 4. Get Tags
    print_section("Tags")
    tags_resp = data_source.get_tags()
    print_result("Get Tags", tags_resp)

    # 5. Get Metaproperties
    print_section("Metaproperties")
    meta_resp = data_source.get_metaproperties()
    print_result("Get Metaproperties", meta_resp)

    # 6. Get Brands
    print_section("Brands")
    brands_resp = data_source.get_brands()
    print_result("Get Brands", brands_resp)

    # 7. Get Account Users
    print_section("Account Users")
    users_resp = data_source.get_account_users()
    print_result("Get Account Users", users_resp)

    print("\n" + "=" * 80)
    print("  All Bynder API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
