# ruff: noqa

"""
Facebook Workplace (Meta Workplace) API Usage Examples

This example demonstrates how to use the Workplace DataSource to interact with
the Facebook Workplace API, covering:
- Authentication (Access Token from admin panel)
- Initializing the Client and DataSource
- Getting Current User
- Listing Community Members
- Listing Community Groups
- Getting Group Feed and Members
- Getting Posts and Comments
- Community Feeds

Prerequisites:
1. Go to the Workplace admin panel
2. Create a custom integration and generate an access token
3. Set WORKPLACE_ACCESS_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.workplace.workplace import (
    WorkplaceClient,
    WorkplaceTokenConfig,
    WorkplaceResponse,
)
from app.sources.external.workplace.workplace import WorkplaceDataSource

# --- Configuration ---
ACCESS_TOKEN = os.getenv("WORKPLACE_ACCESS_TOKEN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: WorkplaceResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle paginated list-type responses (Graph API uses "data" key)
            if isinstance(data, dict) and "data" in data:
                items = data["data"]
                if isinstance(items, list):
                    print(f"   Found {len(items)} items.")
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
    print_section("Initializing Workplace Client")

    if not ACCESS_TOKEN:
        print("  No valid authentication method found.")
        print("   Please set WORKPLACE_ACCESS_TOKEN environment variable")
        print("   (generated from the Workplace admin panel)")
        return

    print("  Using Access Token (Bearer) authentication")
    config = WorkplaceTokenConfig(token=ACCESS_TOKEN)
    client = WorkplaceClient.build_with_config(config)
    data_source = WorkplaceDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        me_resp = await data_source.get_me(fields="id,name,email")
        print_result("Get Me", me_resp)

        # 3. Get Community Members
        print_section("Community Members")
        members_resp = await data_source.get_community_members(
            limit=5, fields="id,name,email"
        )
        print_result("Get Community Members", members_resp)

        # Get a specific user if available
        user_id = None
        if members_resp.success and members_resp.data:
            members_data = members_resp.data.get("data", [])
            if isinstance(members_data, list) and members_data:
                user_id = str(members_data[0].get("id", ""))
                print(f"   Using User: {members_data[0].get('name', 'N/A')} (ID: {user_id})")

        if user_id:
            print_section(f"User Details: {user_id}")
            user_resp = await data_source.get_user(
                user_id=user_id, fields="id,name,email,department"
            )
            print_result("Get User", user_resp)

            # Get User Feed
            print_section("User Feed")
            feed_resp = await data_source.get_user_feed(user_id=user_id, limit=3)
            print_result("Get User Feed", feed_resp)

        # 4. Get Community Groups
        print_section("Community Groups")
        groups_resp = await data_source.get_community_groups(
            limit=5, fields="id,name,description,privacy"
        )
        print_result("Get Community Groups", groups_resp)

        # Explore first group if available
        group_id = None
        if groups_resp.success and groups_resp.data:
            groups_data = groups_resp.data.get("data", [])
            if isinstance(groups_data, list) and groups_data:
                group_id = str(groups_data[0].get("id", ""))
                print(f"   Using Group: {groups_data[0].get('name', 'N/A')} (ID: {group_id})")

        if group_id:
            # Get Group Details
            print_section(f"Group Details: {group_id}")
            group_resp = await data_source.get_group(
                group_id=group_id, fields="id,name,description,privacy,member_count"
            )
            print_result("Get Group", group_resp)

            # Get Group Feed
            print_section("Group Feed")
            group_feed_resp = await data_source.get_group_feed(
                group_id=group_id, limit=3
            )
            print_result("Get Group Feed", group_feed_resp)

            # Get Group Members
            print_section("Group Members")
            group_members_resp = await data_source.get_group_members(
                group_id=group_id, limit=5
            )
            print_result("Get Group Members", group_members_resp)

            # Get a post from group feed if available
            post_id = None
            if group_feed_resp.success and group_feed_resp.data:
                feed_data = group_feed_resp.data.get("data", [])
                if isinstance(feed_data, list) and feed_data:
                    post_id = str(feed_data[0].get("id", ""))
                    print(f"   Using Post ID: {post_id}")

            if post_id:
                # Get Post Details
                print_section(f"Post Details: {post_id}")
                post_resp = await data_source.get_post(
                    post_id=post_id, fields="id,message,created_time,from"
                )
                print_result("Get Post", post_resp)

                # Get Post Comments
                print_section("Post Comments")
                comments_resp = await data_source.get_post_comments(
                    post_id=post_id, limit=5
                )
                print_result("Get Post Comments", comments_resp)

        # 5. Get Community Feeds
        print_section("Community Feeds")
        community_feeds_resp = await data_source.get_community_feeds(limit=5)
        print_result("Get Community Feeds", community_feeds_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Workplace API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
