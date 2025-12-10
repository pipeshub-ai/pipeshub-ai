"""
Example usage of the ZoomDataSource
"""

import sys, os, asyncio

# --- Fix sys.path so "backend.python.…" imports work ---
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../.."))
APP = os.path.join(ROOT, "backend", "python")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if APP not in sys.path:
    sys.path.insert(0, APP)

# Correct imports
from backend.python.app.sources.client.zoom.zoom import ZoomClient, ZoomTokenConfig
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource
        

async def main() -> None:
    token = os.getenv("ZOOM_TOKEN")
    if not token:
        raise Exception("ZOOM_TOKEN not set")

    account_id = os.getenv("ZOOM_ACCOUNT_ID")
    if not account_id:
        raise Exception("ZOOM_ACCOUNT_ID not set")

    # Build wrapper
    wrapper = ZoomClient.build_with_config(
        ZoomTokenConfig(
            base_url="https://api.zoom.us/v2",
            token=token,
        )
    )

    # ❗ Get the underlying REST client
    rest_client = wrapper.get_client()

    # Build datasource correctly
    ds = ZoomDataSource(rest_client)

    print("Testing account_managed_domain:")
    try:
        resp = await ds.users()
        print(resp.response.json())  
    except Exception as e:
        print("Error:", e)


if __name__ == "__main__":
    asyncio.run(main())

