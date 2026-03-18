# ruff: noqa

"""
PlusPlus API Usage Examples

This example demonstrates how to use the PlusPlus DataSource to interact with
the PlusPlus API, covering:
- Authentication (API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing Events, Users, Tracks, Channels, Content
- Fetching specific resources by ID

Prerequisites:
1. Obtain an API key from PlusPlus
2. Set PLUSPLUS_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.plusplus.plusplus import (
    PlusPlusClient,
    PlusPlusTokenConfig,
    PlusPlusResponse,
)
from app.sources.external.plusplus.plusplus import PlusPlusDataSource

# --- Configuration ---
API_KEY = os.getenv("PLUSPLUS_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: PlusPlusResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("events", "users", "tracks", "channels", "content",
                        "tags", "enrollments", "results"):
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
    print_section("Initializing PlusPlus Client")

    if not API_KEY:
        print("  No API key found.")
        print("   Please set PLUSPLUS_API_KEY environment variable.")
        return

    print("  Using API Key authentication")
    config = PlusPlusTokenConfig(token=API_KEY)
    client = PlusPlusClient.build_with_config(config)
    data_source = PlusPlusDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Events
        print_section("Events")
        events_resp = await data_source.list_events(page=1, per_page=10)
        print_result("List Events", events_resp)

        # 3. List Users
        print_section("Users")
        users_resp = await data_source.list_users(page=1, per_page=10)
        print_result("List Users", users_resp)

        # 4. List Tracks
        print_section("Tracks")
        tracks_resp = await data_source.list_tracks(page=1, per_page=10)
        print_result("List Tracks", tracks_resp)

        # 5. List Channels
        print_section("Channels")
        channels_resp = await data_source.list_channels(page=1, per_page=10)
        print_result("List Channels", channels_resp)

        # 6. List Content
        print_section("Content")
        content_resp = await data_source.list_content(page=1, per_page=10)
        print_result("List Content", content_resp)

        # 7. List Tags
        print_section("Tags")
        tags_resp = await data_source.list_tags(page=1, per_page=10)
        print_result("List Tags", tags_resp)

        # 8. List Enrollments
        print_section("Enrollments")
        enrollments_resp = await data_source.list_enrollments(page=1, per_page=10)
        print_result("List Enrollments", enrollments_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All PlusPlus API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
