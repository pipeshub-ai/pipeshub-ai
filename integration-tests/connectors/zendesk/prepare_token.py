"""Mint a Zendesk access token before a live test run.

Uses the client_credentials grant of a confidential OAuth client. Zendesk issues no
refresh token for it, so nothing rotates and nothing is written back between runs.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

INTEGRATION_DIR = Path(__file__).resolve().parents[2]
# Outlives the 300-minute integration job; Zendesk caps access tokens at 2 days.
TOKEN_LIFETIME_SECONDS = 6 * 3600
REQUIRED = (
    "ZENDESK_TEST_SUBDOMAIN",
    "ZENDESK_TEST_CLIENT_ID",
    "ZENDESK_TEST_CLIENT_SECRET",
    "ZENDESK_TEST_ACCESS_TOKEN_FILE",
)


def mint_token(subdomain: str, client_id: str, client_secret: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", subdomain):
        raise ValueError("Invalid Zendesk subdomain")
    try:
        response = httpx.post(
            f"https://{subdomain}.zendesk.com/oauth/tokens",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "read",
                "expires_in": TOKEN_LIFETIME_SECONDS,
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Zendesk token request could not reach the token endpoint"
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not response.is_success:
        error = payload.get("error") if isinstance(payload, dict) else None
        # A public client is refused with unauthorized_client; only confidential ones may use this grant.
        raise RuntimeError(
            f"Zendesk token request failed with HTTP {response.status_code}"
            + (f" ({error})" if isinstance(error, str) else "")
        )
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Zendesk token request returned no access token")
    return access_token


def main() -> int:
    if os.getenv("GITHUB_ACTIONS") != "true":
        load_dotenv(INTEGRATION_DIR / ".env.local", override=False)
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print(f"Missing Zendesk test settings: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        access_token = mint_token(
            os.environ["ZENDESK_TEST_SUBDOMAIN"],
            os.environ["ZENDESK_TEST_CLIENT_ID"],
            os.environ["ZENDESK_TEST_CLIENT_SECRET"],
        )
        token_file = Path(os.environ["ZENDESK_TEST_ACCESS_TOKEN_FILE"])
        token_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(access_token)
        token_file.chmod(0o600)
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Zendesk access token minted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
