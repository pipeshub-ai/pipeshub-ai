"""Exchange and persist the rotating Zendesk token before a live test run."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv, set_key

INTEGRATION_DIR = Path(__file__).resolve().parents[2]
REFRESH_FILE = INTEGRATION_DIR / ".env.refresh_tokens"
REFRESH_ENV = "ZENDESK_TEST_REFRESH_TOKEN"
REQUIRED = (
    "ZENDESK_TEST_SUBDOMAIN",
    "ZENDESK_TEST_CLIENT_ID",
    "ZENDESK_TEST_CLIENT_SECRET",
    REFRESH_ENV,
    "ZENDESK_TEST_ACCESS_TOKEN_FILE",
)


def exchange_token(
    subdomain: str, client_id: str, client_secret: str, refresh_token: str
) -> tuple[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", subdomain):
        raise ValueError("Invalid Zendesk subdomain")
    try:
        response = httpx.post(
            f"https://{subdomain}.zendesk.com/oauth/tokens",
            json={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "expires_in": 86400,
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Zendesk token exchange could not reach the token endpoint"
        ) from exc
    if not response.is_success:
        raise RuntimeError(
            f"Zendesk token exchange failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Zendesk token exchange returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Zendesk token exchange returned an invalid response")
    access_token = payload.get("access_token")
    new_refresh_token = payload.get("refresh_token")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(new_refresh_token, str)
        or not new_refresh_token
    ):
        raise RuntimeError("Zendesk token exchange did not return both rotated tokens")
    return access_token, new_refresh_token


def persist_refresh_token(token: str) -> None:
    if os.getenv("GITHUB_ACTIONS") == "true":
        repository = os.environ.get("GITHUB_REPOSITORY")
        environment = os.environ.get("ZENDESK_TEST_SECRET_ENVIRONMENT")
        if not repository or not environment or not os.environ.get("GH_TOKEN"):
            raise RuntimeError("GitHub secret persistence is not configured")
        for attempt in range(3):
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "secret",
                        "set",
                        REFRESH_ENV,
                        "--env",
                        environment,
                        "--repo",
                        repository,
                    ],
                    input=token,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                if attempt == 2:
                    raise RuntimeError(
                        "Could not save the rotated Zendesk refresh token"
                    ) from exc
            else:
                if result.returncode == 0:
                    return
                if attempt == 2:
                    raise RuntimeError(
                        "Could not save the rotated Zendesk refresh token"
                    )
            time.sleep(2**attempt)
        return

    REFRESH_FILE.touch(mode=0o600, exist_ok=True)
    REFRESH_FILE.chmod(0o600)
    set_key(str(REFRESH_FILE), REFRESH_ENV, token)
    REFRESH_FILE.chmod(0o600)


def main() -> int:
    if os.getenv("GITHUB_ACTIONS") != "true":
        load_dotenv(INTEGRATION_DIR / ".env.local", override=False)
        load_dotenv(REFRESH_FILE, override=True)
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print(f"Missing Zendesk test settings: {', '.join(missing)}", file=sys.stderr)
        return 2
    if os.getenv("GITHUB_ACTIONS") == "true" and not all(
        os.getenv(name)
        for name in ("GH_TOKEN", "GITHUB_REPOSITORY", "ZENDESK_TEST_SECRET_ENVIRONMENT")
    ):
        print("Missing GitHub secret persistence settings", file=sys.stderr)
        return 2

    try:
        access_token, refresh_token = exchange_token(
            os.environ["ZENDESK_TEST_SUBDOMAIN"],
            os.environ["ZENDESK_TEST_CLIENT_ID"],
            os.environ["ZENDESK_TEST_CLIENT_SECRET"],
            os.environ[REFRESH_ENV],
        )
        persist_refresh_token(refresh_token)
        token_file = Path(os.environ["ZENDESK_TEST_ACCESS_TOKEN_FILE"])
        token_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(access_token)
        token_file.chmod(0o600)
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Zendesk access token refreshed and rotated refresh token saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
