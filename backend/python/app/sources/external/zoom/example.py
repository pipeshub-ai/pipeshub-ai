# ruff: noqa
"""
Minimal Zoom Example (PipesHub-style)
Matches Dropbox/Slack example format.
"""

import asyncio
import os
from app.sources.client.zoom.zoom import ZoomClient, ZoomAppKeySecretConfig
from app.sources.external.zoom.zoom_ import ZoomDataSource


async def main():
    # Load credentials from environment (local run only)
    client_id = os.getenv("ZOOM_CLIENT_ID")
    client_secret = os.getenv("ZOOM_CLIENT_SECRET")
    account_id = os.getenv("ZOOM_ACCOUNT_ID")

    if not all([client_id, client_secret, account_id]):
        raise RuntimeError("Missing env vars: ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET / ZOOM_ACCOUNT_ID")

    # Build config
    cfg = ZoomAppKeySecretConfig(
        client_id=client_id,
        client_secret=client_secret,
        account_id=account_id,
        base_url="https://api.zoom.us/v2",
    )

    # Build client
    zoom_client = await ZoomClient.build_with_config(cfg)

    # DataSource
    ds = ZoomDataSource(zoom_client)

    # Basic tests
    print("\nListing users:")
    users = await ds.users_list(page_size=5)
    print(users)

    print("\nListing meetings for 'me':")
    meetings = await ds.list_meetings("me", page_size=5)
    print(meetings)

    print("\nGetting raw /users/me:")
    raw = await ds.raw_request("GET", "/users/me")
    print(raw)


if __name__ == "__main__":
    asyncio.run(main())
