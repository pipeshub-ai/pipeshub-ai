"""Get the token the Notion OAuth suite connects with: a one-time browser consent.

Notion has no way to mint a user's OAuth token without that user approving the
integration in a browser, so this runs once by hand, not in a test:

    cd integration-tests
    python -m helper.notion_oauth_consent [--redirect-uri http://localhost:8765/notion/callback]

1. Add the redirect URI to the public integration first (notion.so/profile/integrations,
   the integration, "OAuth domain & URIs"): Notion refuses any URI it does not list.
2. Sign in as the Notion user the suite should link to, pick the pages to share, approve.
   For the link to exist that user's email must differ from PIPESHUB_TEST_USER_EMAIL,
   and the integration needs "Read user information including email addresses".

Reads NOTION_OAUTH_CLIENT_ID / NOTION_OAUTH_CLIENT_SECRET from .env.refresh_tokens and
writes NOTION_REFRESH_TOKEN back there (NOTION_OAUTH_ACCESS_TOKEN when Notion issues no
refresh token). Nothing is printed but who approved.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv, set_key

ENV_FILE = Path(__file__).resolve().parent.parent / ".env.refresh_tokens"
AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"
DEFAULT_REDIRECT_URI = "http://localhost:8765/notion/callback"


def _wait_for_redirect(redirect_uri: str) -> dict[str, str]:
    """Serve the redirect URI until Notion sends the browser back to it; return the query."""
    target = urlparse(redirect_uri)
    received: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the http.server hook name
            url = urlparse(self.path)
            if url.path != target.path:
                self.send_error(404)
                return
            received.update({k: v[0] for k, v in parse_qs(url.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Notion answered. You can close this tab.")

        def log_message(self, *_: object) -> None:
            pass

    server = HTTPServer((target.hostname or "localhost", target.port or 80), _Handler)
    try:
        while "code" not in received and "error" not in received:
            server.handle_request()
    finally:
        server.server_close()
    return received


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    client_id = os.getenv("NOTION_OAUTH_CLIENT_ID")
    client_secret = os.getenv("NOTION_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(f"Set NOTION_OAUTH_CLIENT_ID and NOTION_OAUTH_CLIENT_SECRET in {ENV_FILE}", file=sys.stderr)
        return 1

    state = secrets.token_urlsafe(16)
    url = f"{AUTHORIZE_URL}?" + urlencode({
        "client_id": client_id, "response_type": "code", "owner": "user",
        "redirect_uri": args.redirect_uri, "state": state,
    })
    print(f"Open this in a browser, sign in to Notion and approve the integration:\n\n  {url}\n")
    webbrowser.open(url)

    reply = _wait_for_redirect(args.redirect_uri)
    if reply.get("state") != state:
        print("Notion's answer does not carry the state this run sent; not using it.", file=sys.stderr)
        return 1
    if "error" in reply:
        print(f"Notion refused: {reply['error']}", file=sys.stderr)
        return 1

    # Notion takes the client credentials only in a Basic header
    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        json={"grant_type": "authorization_code", "code": reply["code"], "redirect_uri": args.redirect_uri},
        timeout=30,
    )
    if not resp.ok:
        print(f"Token exchange failed: HTTP {resp.status_code} {resp.text}", file=sys.stderr)
        return 1
    token = resp.json()

    if token.get("refresh_token"):
        set_key(str(ENV_FILE), "NOTION_REFRESH_TOKEN", token["refresh_token"])
        stored = "NOTION_REFRESH_TOKEN"
    else:
        set_key(str(ENV_FILE), "NOTION_OAUTH_ACCESS_TOKEN", token["access_token"])
        stored = "NOTION_OAUTH_ACCESS_TOKEN"

    owner = (token.get("owner") or {}).get("user") or {}
    email = (owner.get("person") or {}).get("email") or (
        "no email: enable 'Read user information including email addresses' on the integration"
    )
    print(f"Approved by {owner.get('name')} <{email}> in workspace {token.get('workspace_name')!r}.")
    print(f"Wrote {stored} to {ENV_FILE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
