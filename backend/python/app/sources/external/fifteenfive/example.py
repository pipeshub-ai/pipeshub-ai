# ruff: noqa

"""
15Five API Usage Examples

This example demonstrates how to use the 15Five DataSource to interact with
the 15Five API, covering:
- Authentication (API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing Users, Reports, Reviews, Objectives
- Fetching Pulses, Groups, Departments, High-Fives, One-on-Ones

Prerequisites:
1. Obtain an API key from 15Five (Settings > API)
2. Set FIFTEENFIVE_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.fifteenfive.fifteenfive import (
    FifteenFiveClient,
    FifteenFiveTokenConfig,
    FifteenFiveResponse,
)
from app.sources.external.fifteenfive.fifteenfive import FifteenFiveDataSource

# --- Configuration ---
API_KEY = os.getenv("FIFTEENFIVE_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: FifteenFiveResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("results", "users", "reports", "reviews", "objectives",
                        "pulses", "groups", "departments", "high_fives", "one_on_ones"):
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
    print_section("Initializing 15Five Client")

    if not API_KEY:
        print("  No API key found.")
        print("   Please set FIFTEENFIVE_API_KEY environment variable.")
        return

    print("  Using API Key authentication")
    config = FifteenFiveTokenConfig(token=API_KEY)
    client = FifteenFiveClient.build_with_config(config)
    data_source = FifteenFiveDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = await data_source.list_users(page=1, page_size=10)
        print_result("List Users", users_resp)

        # 3. List Reports
        print_section("Reports")
        reports_resp = await data_source.list_reports(page=1, page_size=10)
        print_result("List Reports", reports_resp)

        # 4. List Reviews
        print_section("Reviews")
        reviews_resp = await data_source.list_reviews(page=1, page_size=10)
        print_result("List Reviews", reviews_resp)

        # 5. List Objectives
        print_section("Objectives")
        objectives_resp = await data_source.list_objectives(page=1, page_size=10)
        print_result("List Objectives", objectives_resp)

        # 6. List Pulses
        print_section("Pulses")
        pulses_resp = await data_source.list_pulses(page=1, page_size=10)
        print_result("List Pulses", pulses_resp)

        # 7. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups(page=1, page_size=10)
        print_result("List Groups", groups_resp)

        # 8. List Departments
        print_section("Departments")
        depts_resp = await data_source.list_departments(page=1, page_size=10)
        print_result("List Departments", depts_resp)

        # 9. List High-Fives
        print_section("High-Fives")
        hf_resp = await data_source.list_high_fives(page=1, page_size=10)
        print_result("List High-Fives", hf_resp)

        # 10. List One-on-Ones
        print_section("One-on-Ones")
        ooo_resp = await data_source.list_one_on_ones(page=1, page_size=10)
        print_result("List One-on-Ones", ooo_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All 15Five API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
