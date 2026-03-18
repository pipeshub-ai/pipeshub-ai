# ruff: noqa

"""
Adobe Experience Manager (AEM as Cloud Service) API Usage Examples

This example demonstrates how to use the AEM DataSource to interact with
the AEM API, covering:
- Authentication (Bearer Token)
- Initializing the Client and DataSource
- Listing and retrieving DAM assets
- Browsing DAM content
- Searching authorizables (users/groups)
- Running QueryBuilder queries
- Listing packages

Prerequisites:
1. Obtain a Bearer token from Adobe Developer Console or via service account JWT
2. Set AEM_TOKEN and AEM_INSTANCE environment variables
   AEM_INSTANCE should be the instance identifier
   (e.g., "author-p12345-e67890" for author-p12345-e67890.adobeaemcloud.com)
"""

import asyncio
import json
import os

from app.sources.client.adobeaem.adobeaem import (
    AdobeAEMClient,
    AdobeAEMTokenConfig,
    AdobeAEMResponse,
)
from app.sources.external.adobeaem.adobeaem import AdobeAEMDataSource

# --- Configuration ---
TOKEN = os.getenv("AEM_TOKEN")
INSTANCE = os.getenv("AEM_INSTANCE", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: AdobeAEMResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                for key in ("entities", "assets", "hits", "results",
                            "authorizables", "packages"):
                    if key in data:
                        items = data[key]
                        if isinstance(items, list):
                            print(f"   Found {len(items)} {key}.")
                            if items:
                                print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                        return
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    if not INSTANCE:
        print("  AEM_INSTANCE environment variable is required.")
        print("   Example: export AEM_INSTANCE=author-p12345-e67890")
        return

    # 1. Initialize Client
    print_section("Initializing AEM Client")

    if not TOKEN:
        print("  No valid authentication method found.")
        print("   Please set AEM_TOKEN environment variable.")
        return

    print("  Using Bearer Token authentication")
    config = AdobeAEMTokenConfig(token=TOKEN, instance=INSTANCE)
    client = AdobeAEMClient.build_with_config(config)
    data_source = AdobeAEMDataSource(client)
    print(f"Client initialized successfully (instance: {INSTANCE}).")

    try:
        # 2. List Assets
        print_section("Assets")
        assets_resp = await data_source.list_assets(limit=10)
        print_result("List Assets", assets_resp)

        # Try to get a specific asset path
        asset_path = None
        if assets_resp.success and assets_resp.data and isinstance(assets_resp.data, dict):
            entities = assets_resp.data.get("entities", [])
            if isinstance(entities, list) and entities:
                props = entities[0].get("properties", {})
                if isinstance(props, dict):
                    asset_path = props.get("name", "")
                    if asset_path:
                        print(f"   Using Asset path: {asset_path}")

        if asset_path:
            print_section("Asset Details")
            asset_resp = await data_source.get_asset(asset_path)
            print_result("Get Asset", asset_resp)

        # 3. Browse DAM Content
        print_section("DAM Content")
        dam_resp = await data_source.get_dam_content(limit=10)
        print_result("Get DAM Content", dam_resp)

        # 4. Search Authorizables
        print_section("Search Authorizables")
        auth_resp = await data_source.search_authorizables(query="admin")
        print_result("Search Authorizables", auth_resp)

        # 5. QueryBuilder Query
        print_section("QueryBuilder - DAM Assets")
        qb_resp = await data_source.query_builder(
            path="/content/dam",
            type="dam:Asset",
            p_limit=10,
        )
        print_result("QueryBuilder Query", qb_resp)

        # 6. Package List
        print_section("Packages")
        pkg_resp = await data_source.get_package_list()
        print_result("Get Packages", pkg_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All AEM API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
