# ruff: noqa
"""
HackerNews API Usage Examples

This example demonstrates how to use the HackerNewsDataSource to interact
with the official HackerNews API, covering:
- Live data (max item id, top stories)
- Fetching a single item (story) by id
- Fetching a user profile

The HackerNews API is public and read-only — no credentials, tokens, or
environment variables are required to run this example.
"""

import asyncio

from app.sources.client.hackernews.hackernews import HackerNewsClient
from app.sources.external.hackernews.hackernews import HackerNewsDataSource
from app.sources.external.dropbox.pretty_print import to_pretty_json


async def main() -> None:
    """Simple example of using HackerNewsDataSource to call the API."""
    try:
        client = await HackerNewsClient.build_and_validate()
    except ValueError as e:
        print("Error: Failed to initialize HackerNews client.")
        print(f"Details: {e}")
        return

    data_source = HackerNewsDataSource(client)

    print("\nCurrent max item id:")
    max_item = await data_source.get_max_item_id()
    print(to_pretty_json(max_item))

    print("\nTop 5 story ids:")
    top_stories = await data_source.get_top_stories()
    top_ids = (top_stories.data or [])[:5]
    print(top_ids)

    if top_ids:
        print(f"\nFirst top story (id={top_ids[0]}):")
        story = await data_source.get_item(item_id=top_ids[0])
        print(to_pretty_json(story))

    print("\nUser profile for 'pg':")
    user = await data_source.get_user(username="pg")
    print(to_pretty_json(user))


if __name__ == "__main__":
    asyncio.run(main())
