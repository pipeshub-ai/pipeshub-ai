"""
Example usage of the ZoomDataSource with S2S OAuth
"""
"""


How to run the Zoom code generator:

    python backend/python/code-generator/zoom.py \
        --spec-dir backend/python/code-generator/zoom_specs \
        --out-dir backend/python/app/sources/external/zoom \
        --overwrite

How to run this example:

    python backend/python/app/sources/external/zoom/example.py
"""

import sys, os, asyncio

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../.."))
APP = os.path.join(ROOT, "backend", "python")

if ROOT not in sys.path: sys.path.insert(0, ROOT)
if APP not in sys.path: sys.path.insert(0, APP)

from backend.python.app.sources.client.zoom.zoom import (
    ZoomClient,
    ZoomServerToServerConfig,
)
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource


async def main():

    ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
    CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
    CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")

    if not ACCOUNT_ID or not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Missing S2S Zoom credentials in env variables")

    # Build wrapper using S2S config
    wrapper = ZoomClient.build_with_config(
        ZoomServerToServerConfig(
            account_id=ACCOUNT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
    )

    # Get REST client
    rest_client = wrapper.get_client()

    # Create datasource
    ds = ZoomDataSource(rest_client)

    print("Calling Zoom API with S2S token generation...")

    # First API call → automatically generates token inside the client
    resp = await ds.users()
    print("\n🔹 Raw Response Object:")
    print(resp)
    try:
        print("\n🔹 Parsed JSON from response:")
        print(resp.json())
    except Exception as e:
        print("Could not parse JSON:", e)

if __name__ == "__main__":
    asyncio.run(main())
