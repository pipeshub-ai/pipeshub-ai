"""Example usage for the Zoom datasource.

This example demonstrates token-based usage via ZoomClient.build_with_config().
"""

import sys
import os
import asyncio

# PATCH IMPORT ROOT (go up to project root)
ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../../../..")
)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Standard imports
from app.sources.client.zoom.zoom import ZoomClient, ZoomTokenConfig   # type: ignore
from app.sources.external.zoom.zoom import ZoomDataSource             # type: ignore


async def main() -> None:
    # Example: Using a static Zoom OAuth token
    config = ZoomTokenConfig(
        base_url="https://api.zoom.us/v2",
        token="your_zoom_oauth_token_here",
    )

    # Build high-level wrapper
    zoom_client = ZoomClient.build_with_config(config)

    # ❗ IMPORTANT: Extract the underlying REST client
    rest_client = zoom_client.get_client()

    # Datasource MUST receive the REST client, not ZoomClient wrapper
    ds = ZoomDataSource(rest_client)

    # Try a simple API call
    try:
        resp = await ds.account_settings(accountId="REPLACE_WITH_ACCOUNT_ID")

        print("Response:", resp)
    except Exception as e:
        print("Error:", e)

    # Close if supported
    close = getattr(rest_client, "close", None)
    if callable(close):
        await close()


if __name__ == "__main__":
    asyncio.run(main())
