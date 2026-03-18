# ruff: noqa

"""
Haystack API Usage Examples

This example demonstrates how to use the Haystack DataSource to interact with
the Haystack API v1, covering:
- Authentication (API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing People, Teams, Locations, Departments
- Fetching Announcements and Pages
- Searching content

Prerequisites:
1. Obtain an API key from Haystack admin settings
2. Set HAYSTACK_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.haystack.haystack import (
    HaystackClient,
    HaystackTokenConfig,
    HaystackResponse,
)
from app.sources.external.haystack.haystack import HaystackDataSource

# --- Configuration ---
API_KEY = os.getenv("HAYSTACK_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: HaystackResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                for key in ("people", "teams", "locations", "departments",
                            "announcements", "pages", "results"):
                    if key in data:
                        items = data[key]
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
    # 1. Initialize Client
    print_section("Initializing Haystack Client")

    if not API_KEY:
        print("  No valid authentication method found.")
        print("   Please set HAYSTACK_API_KEY environment variable.")
        return

    print("  Using API Key authentication")
    config = HaystackTokenConfig(token=API_KEY)
    client = HaystackClient.build_with_config(config)
    data_source = HaystackDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get People
        print_section("People")
        people_resp = await data_source.get_people(limit=10)
        print_result("Get People", people_resp)

        person_id = None
        if people_resp.success and people_resp.data:
            data = people_resp.data
            items = data if isinstance(data, list) else data.get("people", []) if isinstance(data, dict) else []
            if items:
                person_id = str(items[0].get("id"))
                print(f"   Using Person ID: {person_id}")

        if person_id:
            print_section("Person Details")
            person_resp = await data_source.get_person(person_id)
            print_result("Get Person", person_resp)

        # 3. Get Teams
        print_section("Teams")
        teams_resp = await data_source.get_teams(limit=10)
        print_result("Get Teams", teams_resp)

        # 4. Get Locations
        print_section("Locations")
        locations_resp = await data_source.get_locations(limit=10)
        print_result("Get Locations", locations_resp)

        # 5. Get Departments
        print_section("Departments")
        departments_resp = await data_source.get_departments(limit=10)
        print_result("Get Departments", departments_resp)

        # 6. Get Announcements
        print_section("Announcements")
        announcements_resp = await data_source.get_announcements(limit=10)
        print_result("Get Announcements", announcements_resp)

        # 7. Get Pages
        print_section("Pages")
        pages_resp = await data_source.get_pages(limit=10)
        print_result("Get Pages", pages_resp)

        # 8. Search
        print_section("Search")
        search_resp = await data_source.search(q="team", limit=10)
        print_result("Search", search_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Haystack API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
