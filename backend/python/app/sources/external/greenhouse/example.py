# ruff: noqa

"""
Greenhouse Harvest API Usage Examples

This example demonstrates how to use the Greenhouse DataSource to interact with
the Greenhouse Harvest API, covering:
- Authentication (API Key via HTTP Basic Auth)
- Initializing the Client and DataSource
- Listing Candidates
- Listing Jobs
- Listing Departments
- Listing Users

Prerequisites:
1. Create a Greenhouse Harvest API key at:
   https://app.greenhouse.io/configure/dev_center/credentials
2. Set GREENHOUSE_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.greenhouse.greenhouse import (
    GreenhouseApiKeyConfig,
    GreenhouseClient,
    GreenhouseResponse,
)
from app.sources.external.greenhouse.greenhouse import GreenhouseDataSource

# --- Configuration ---
API_KEY = os.getenv("GREENHOUSE_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: GreenhouseResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses (Greenhouse returns arrays at top level)
            for key in ("candidates", "applications", "jobs", "departments",
                        "offices", "users"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Greenhouse list endpoints return arrays directly
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
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
    print_section("Initializing Greenhouse Client")

    if not API_KEY:
        print("  No valid authentication method found.")
        print("   Please set the following environment variable:")
        print("   - GREENHOUSE_API_KEY (Harvest API key)")
        return

    print("  Using API Key authentication")
    config = GreenhouseApiKeyConfig(api_key=API_KEY)

    client = GreenhouseClient.build_with_config(config)
    data_source = GreenhouseDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Candidates
        print_section("Candidates")
        candidates_resp = await data_source.list_candidates(per_page=5)
        print_result("List Candidates", candidates_resp)

        # 3. List Jobs
        print_section("Jobs")
        jobs_resp = await data_source.list_jobs(per_page=5)
        print_result("List Jobs", jobs_resp)

        # 4. List Departments
        print_section("Departments")
        departments_resp = await data_source.list_departments(per_page=5)
        print_result("List Departments", departments_resp)

        # 5. List Users
        print_section("Users")
        users_resp = await data_source.list_users(per_page=5)
        print_result("List Users", users_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Greenhouse API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
