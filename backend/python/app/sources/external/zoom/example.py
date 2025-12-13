"""
ZoomDataSource Example (OAuth Authorization Code Flow)

HOW TO RUN THIS EXAMPLE:

1) From the repository root, run:
       python backend/python/code-generator/zoom.py

   This generates files into:
       backend/python/code-generator/output/zoom/

2) Copy the generated files into the external runtime folder:
       cp backend/python/code-generator/output/zoom/*           backend/python/app/sources/external/zoom/

3) Move into the external Zoom folder:
       cd backend/python/app/sources/external/zoom

4) Run the example:
       python example.py

   - A browser window will open
   - Login and authorize the Zoom OAuth app
   - Zoom will redirect to http://localhost:8080/callback
     (the page may show an error — this is OK)
   - Copy the `code=` value from the browser URL
   - Paste it into the terminal when prompted

This example demonstrates:
- OAuth Authorization Code flow
- Token exchange
- Calling real Zoom APIs (users, groups, chat, account)
- Graceful handling of feature-gated APIs
"""

import os
import sys
import asyncio
import webbrowser
from urllib.parse import urlencode

# -------------------------------------------------
# Ensure repo root + backend/python are importable
# -------------------------------------------------

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../.."))
APP = os.path.join(ROOT, "backend", "python")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if APP not in sys.path:
    sys.path.insert(0, APP)

from backend.python.app.sources.client.zoom.zoom import (
    ZoomClient,
    ZoomOAuthConfig,
)
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource

AUTH_URL = "https://zoom.us/oauth/authorize"


async def main() -> None:
    CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
    CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("ZOOM_REDIRECT_URI", "http://localhost:8080/callback")

    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Set ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET")

    wrapper = ZoomClient.build_with_config(
        ZoomOAuthConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
        )
    )

    rest = wrapper.get_client()
    ds = ZoomDataSource(rest)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    }

    url = f"{AUTH_URL}?{urlencode(params)}"
    print("Open this URL & authorize:\n", url)

    try:
        webbrowser.open(url)
    except Exception:
        pass

    code = input("\nPaste the ?code= value here: ").strip()
    if not code:
        raise RuntimeError("No authorization code provided")

    await rest.exchange_code_for_token(code)

    print("\nCalling users() ...")
    try:
        r = await ds.users()
        print(r.json())
    except Exception as e:
        print("users() failed:", e)

    print("\nCalling groups() ...")
    try:
        r = await ds.groups()
        print(r.json())
    except Exception as e:
        print("groups() failed:", e)

    print("\nCalling get_chat_sessions() ...")
    try:
        r = await ds.get_chat_sessions()
        print(r.json())
    except Exception as e:
        print("get_chat_sessions() failed:", e)

    print("\nCalling get_a_billing_account() ...")
    try:
        r = await ds.get_a_billing_account()
        print(r.json())
    except Exception as e:
        print("get_a_billing_account() failed:", e)


if __name__ == "__main__":
    asyncio.run(main())
