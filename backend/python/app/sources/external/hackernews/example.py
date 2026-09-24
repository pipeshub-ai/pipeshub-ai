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
import json
from typing import Any

from app.sources.client.hackernews.hackernews import HackerNewsClient
from app.sources.external.hackernews.hackernews import HackerNewsDataSource


def _truncate_lists(obj: Any, limit: int = 5) -> Any:
    """Recursively cap list length in a parsed JSON structure.

    Some HackerNews responses are huge — a long-time user's `submitted`
    array can have 10,000+ ids, and a popular story's `kids` (comment ids)
    can run to hundreds. Printed in full, either floods the terminal past
    its scrollback buffer. This keeps the real shape of the response
    visible without dumping every entry.
    """
    if isinstance(obj, list):
        truncated = [_truncate_lists(item, limit) for item in obj[:limit]]
        if len(obj) > limit:
            truncated.append(f"... and {len(obj) - limit} more")
        return truncated
    if isinstance(obj, dict):
        return {key: _truncate_lists(value, limit) for key, value in obj.items()}
    return obj


def _print_response(label: str, resp: Any) -> None:
    """Pretty-print a HackerNewsResponse with any long lists truncated."""
    print(f"\n{label}")
    print(json.dumps(_truncate_lists(resp.to_dict()), indent=2, ensure_ascii=False))


async def main() -> None:
    """Simple example of using HackerNewsDataSource to call the API."""
    try:
        client = await HackerNewsClient.build_and_validate()
    except ValueError as e:
        print("Error: Failed to initialize HackerNews client.")
        print(f"Details: {e}")
        return

    data_source = HackerNewsDataSource(client)

    max_item = await data_source.get_max_item_id()
    _print_response("Current max item id:", max_item)

    top_stories = await data_source.get_top_stories()
    top_ids = (top_stories.data or [])[:5]
    print("\nTop 5 story ids:")
    print(top_ids)

    if top_ids:
        story = await data_source.get_item(item_id=top_ids[0])
        _print_response(f"First top story (id={top_ids[0]}):", story)

    user = await data_source.get_user(username="pg")
    _print_response("User profile for 'pg':", user)


if __name__ == "__main__":
    asyncio.run(main())
