# ruff: noqa

"""
Benchling API Usage Examples

This example demonstrates how to use the Benchling DataSource to interact
with the Benchling API via the official benchling-sdk, covering:
- Authentication (API Key)
- Initializing the Client and DataSource
- Listing notebook entries, folders, schemas
- Fetching custom entities, DNA sequences, users, projects

Prerequisites:
1. Have a Benchling tenant with an API key
2. Set the following environment variables:
   - BENCHLING_API_KEY: Your API key
   - BENCHLING_TENANT_URL: Full tenant URL (e.g. https://your-tenant.benchling.com)
"""

import json
import os

from app.sources.client.benchling.benchling import (
    BenchlingApiKeyConfig,
    BenchlingClient,
    BenchlingResponse,
)
from app.sources.external.benchling.benchling import BenchlingDataSource

# --- Configuration ---
API_KEY = os.getenv("BENCHLING_API_KEY")
TENANT_URL = os.getenv("BENCHLING_TENANT_URL", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: BenchlingResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {str(data[0])[:400]}...")
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
    # 1. Initialize Client
    print_section("Initializing Benchling Client")

    if not API_KEY or not TENANT_URL:
        print("  Missing required environment variables.")
        print("   Please set:")
        print("   - BENCHLING_API_KEY")
        print("   - BENCHLING_TENANT_URL (e.g. https://your-tenant.benchling.com)")
        return

    print("  Using API Key authentication")
    config = BenchlingApiKeyConfig(
        api_key=API_KEY,
        tenant_url=TENANT_URL,
    )

    client = BenchlingClient.build_with_config(config)
    data_source = BenchlingDataSource(client)
    print(f"  Client initialized successfully (tenant: {TENANT_URL})")

    # 2. List Entries
    print_section("Notebook Entries")
    entries_resp = data_source.list_entries()
    print_result("List Entries", entries_resp)

    # 3. List Folders
    print_section("Folders")
    folders_resp = data_source.list_folders()
    print_result("List Folders", folders_resp)

    # 4. List Entity Schemas
    print_section("Entity Schemas")
    schemas_resp = data_source.list_entity_schemas()
    print_result("List Entity Schemas", schemas_resp)

    # 5. List Custom Entities
    print_section("Custom Entities")
    entities_resp = data_source.list_custom_entities()
    print_result("List Custom Entities", entities_resp)

    # 6. List DNA Sequences
    print_section("DNA Sequences")
    dna_resp = data_source.list_dna_sequences()
    print_result("List DNA Sequences", dna_resp)

    # 7. List Users
    print_section("Users")
    users_resp = data_source.list_users()
    print_result("List Users", users_resp)

    # 8. List Projects
    print_section("Projects")
    projects_resp = data_source.list_projects()
    print_result("List Projects", projects_resp)

    print("\n" + "=" * 80)
    print("  All Benchling API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
