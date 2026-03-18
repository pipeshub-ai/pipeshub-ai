# ruff: noqa
"""
Slab API Usage Examples

This example demonstrates how to use the Slab DataSource to interact with
the Slab GraphQL API, covering:
- Authentication (API Token)
- Initializing the Client and DataSource
- Fetching Organization Info
- Listing Users
- Listing and Getting Posts
- Listing and Getting Topics
- Searching Posts

Prerequisites:
1. Generate a Slab API token at your organization's Slab settings
2. Set SLAB_API_TOKEN environment variable

API Reference: https://slab.com/api/
"""

import asyncio
import json
import os

from app.sources.client.slab.slab import SlabClient, SlabTokenConfig
from app.sources.external.slab.slab import SlabDataSource


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


async def main() -> None:
    """Example usage of Slab API."""
    SLAB_API_TOKEN = os.getenv("SLAB_API_TOKEN")

    if not SLAB_API_TOKEN:
        print("Please set SLAB_API_TOKEN environment variable")
        print("   Get your token from your Slab organization settings")
        return

    # Initialize Slab client and data source
    client = SlabClient.build_with_config(SlabTokenConfig(token=SLAB_API_TOKEN))
    data_source = SlabDataSource(client)

    try:
        # 1. Validate connection - Get organization info
        print_section("Organization Info")
        org_response = await data_source.organization()
        if not org_response.success:
            print(f"Failed to connect to Slab API: {org_response.message}")
            if org_response.errors:
                for error in org_response.errors:
                    print(f"  Error: {error.message}")
            return

        org_data = org_response.data.get("organization", {}) if org_response.data else {}
        if org_data:
            print(f"  Connected successfully!")
            print(f"  Organization: {org_data.get('name', 'Unknown')} (ID: {org_data.get('id', 'Unknown')})")
            print(f"  Hostname: {org_data.get('hostname', 'Unknown')}")
        else:
            print("  Connection successful but no organization data returned")

        # 2. List users
        print_section("Users")
        users_response = await data_source.users()
        if users_response.success:
            org = users_response.data.get("organization", {}) if users_response.data else {}
            members = org.get("members", []) if org else []
            print(f"  Found {len(members)} users:")
            for user in members[:5]:
                status = "active" if not user.get("deactivatedAt") else "deactivated"
                print(f"    - {user.get('name')} ({user.get('email')}) [{status}]")
        else:
            print(f"  Failed to get users: {users_response.message}")

        # 3. List topics
        print_section("Topics")
        topics_response = await data_source.topics()
        if topics_response.success:
            topics = topics_response.data.get("topics", []) if topics_response.data else []
            print(f"  Found {len(topics)} topics:")
            for topic in topics[:5]:
                print(f"    - {topic.get('name')} (ID: {topic.get('id')}, Posts: {topic.get('postCount', 0)})")

            # Get details of first topic
            if topics:
                first_topic_id = topics[0].get("id")
                print_section(f"Topic Details: {topics[0].get('name')}")
                topic_response = await data_source.topic(id=first_topic_id)
                if topic_response.success:
                    topic_data = topic_response.data.get("topic", {}) if topic_response.data else {}
                    posts = topic_data.get("posts", [])
                    print(f"  Posts in topic: {len(posts)}")
                    for post in posts[:3]:
                        print(f"    - {post.get('title')} (ID: {post.get('id')})")
                else:
                    print(f"  Failed to get topic: {topic_response.message}")
        else:
            print(f"  Failed to get topics: {topics_response.message}")

        # 4. List published posts
        print_section("Published Posts")
        posts_response = await data_source.posts(status="PUBLISHED")
        if posts_response.success:
            posts = posts_response.data.get("posts", []) if posts_response.data else []
            print(f"  Found {len(posts)} published posts:")
            for post in posts[:5]:
                creator = post.get("creator", {})
                creator_name = creator.get("name", "Unknown") if creator else "Unknown"
                print(f"    - {post.get('title')} (by {creator_name})")

            # Get details of first post
            if posts:
                first_post_id = posts[0].get("id")
                print_section(f"Post Details: {posts[0].get('title')}")
                post_response = await data_source.post(id=first_post_id)
                if post_response.success:
                    post_data = post_response.data.get("post", {}) if post_response.data else {}
                    topics = post_data.get("topics", [])
                    print(f"  Topics: {', '.join(t.get('name', '') for t in topics) if topics else 'None'}")
                    print(f"  Published: {post_data.get('publishedAt', 'N/A')}")
                    print(f"  Updated: {post_data.get('updatedAt', 'N/A')}")
                else:
                    print(f"  Failed to get post: {post_response.message}")
        else:
            print(f"  Failed to get posts: {posts_response.message}")

        # 5. Search posts
        print_section("Search Posts")
        search_response = await data_source.search_posts(query="getting started")
        if search_response.success:
            results = search_response.data.get("searchPosts", []) if search_response.data else []
            print(f"  Found {len(results)} results for 'getting started':")
            for result in results[:5]:
                print(f"    - {result.get('title')} (ID: {result.get('id')})")
        else:
            print(f"  Search failed: {search_response.message}")

    finally:
        # Close the client
        await client.get_client().close()

    print("\n" + "=" * 80)
    print("  All Slab API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
