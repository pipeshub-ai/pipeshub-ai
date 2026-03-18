# ruff: noqa

"""
DataStax Astra DB API Usage Examples

This example demonstrates how to use the DataStax DataSource to interact
with DataStax Astra DB via the official astrapy SDK, covering:
- Authentication (Application Token)
- Initializing the Client and DataSource
- Listing collections
- Finding, inserting, and counting documents

Prerequisites:
1. Create an Astra DB database at https://astra.datastax.com
2. Generate an Application Token
3. Set the following environment variables:
   - DATASTAX_TOKEN: Application token (e.g. AstraCS:...)
   - DATASTAX_API_ENDPOINT: Database API endpoint URL
"""

import json
import os

from app.sources.client.datastax.datastax import (
    DataStaxClient,
    DataStaxResponse,
    DataStaxTokenConfig,
)
from app.sources.external.datastax.datastax import DataStaxDataSource

# --- Configuration ---
TOKEN = os.getenv("DATASTAX_TOKEN")
API_ENDPOINT = os.getenv("DATASTAX_API_ENDPOINT")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: DataStaxResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Initialize Client
    print_section("Initializing DataStax Client")

    if not TOKEN or not API_ENDPOINT:
        print("  Missing required environment variables.")
        print("   Please set:")
        print("   - DATASTAX_TOKEN (Application Token, e.g. AstraCS:...)")
        print("   - DATASTAX_API_ENDPOINT (Database API endpoint URL)")
        return

    print("  Using Application Token authentication")
    config = DataStaxTokenConfig(
        token=TOKEN,
        api_endpoint=API_ENDPOINT,
    )

    client = DataStaxClient.build_with_config(config)
    data_source = DataStaxDataSource(client)
    print("  Client initialized successfully.")

    # 2. List Collections
    print_section("Collections")
    collections_resp = data_source.list_collections()
    print_result("List Collections", collections_resp)

    # Extract first collection for further exploration
    collection_name = None
    if collections_resp.success and collections_resp.data:
        data = collections_resp.data
        if isinstance(data, list) and data:
            collection_name = str(data[0])

    if collection_name:
        print(f"   Using collection: {collection_name}")

        # 3. Find Documents
        print_section(f"Documents in {collection_name}")
        docs_resp = data_source.find_documents(
            collection_name=collection_name,
            limit=5,
        )
        print_result("Find Documents", docs_resp)

        # 4. Count Documents
        print_section(f"Count Documents in {collection_name}")
        count_resp = data_source.count_documents(
            collection_name=collection_name,
        )
        print_result("Count Documents", count_resp)
    else:
        print("   No collections found. Skipping document operations.")

    print("\n" + "=" * 80)
    print("  All DataStax API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
