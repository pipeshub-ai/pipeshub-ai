"""
Helpers for creating a second PipeshubClient as a different (non-admin) user.

Creates a test user, seeds a password in MongoDB, authenticates, creates an
OAuth app as that user, then yields a PipeshubClient configured with those
second credentials.  All resources are cleaned up on teardown.
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
from typing import Iterator

import bcrypt
import pytest
import requests
from pymongo import MongoClient

from config import MONGO_DB_NAME, MONGO_URI, TEST_USER_PASSWORD
from pipeshub_client import PipeshubClient

logger = logging.getLogger("second-user-auth")


def _random_email() -> str:
    uid = uuid.uuid4().hex[:12]
    return f"integration-test-{uid}@test-pipeshub.com"


def _create_test_user(pipeshub_client: PipeshubClient, timeout: int) -> dict:
    email = _random_email()
    full_name = f"Integration Test {uuid.uuid4().hex[:8]}"
    resp = pipeshub_client.request(
        "POST",
        f"{pipeshub_client.base_url}/api/v1/users",
        json={"fullName": full_name, "email": email},
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"createUser failed: HTTP {resp.status_code} -- "
            f"ensure the OAuth app has `user:invite` scope: {resp.text}"
        )
    user = resp.json()
    logger.info("Created test user %s (id=%s)", email, user.get("_id"))
    return user


def _seed_password(org_id: str, user_id: str) -> None:
    hashed = bcrypt.hashpw(TEST_USER_PASSWORD.encode(), bcrypt.gensalt()).decode()
    client = MongoClient(MONGO_URI)
    try:
        client[MONGO_DB_NAME].userCredentials.insert_one({
            "userId": user_id,
            "orgId": org_id,
            "hashedPassword": hashed,
            "ipAddress": "127.0.0.1",
            "wrongCredentialCount": 0,
            "isBlocked": False,
            "forceNewPasswordGeneration": False,
            "isDeleted": False,
            "createdAt": datetime.datetime.now(datetime.timezone.utc),
            "updatedAt": datetime.datetime.now(datetime.timezone.utc),
        })
    finally:
        client.close()


def _cleanup_credentials(org_id: str, user_id: str) -> None:
    try:
        client = MongoClient(MONGO_URI)
        try:
            client[MONGO_DB_NAME].userCredentials.delete_one(
                {"userId": user_id, "orgId": org_id}
            )
        finally:
            client.close()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to clean up credentials for user %s", user_id)


def _login(base_url: str, email: str, timeout: int) -> str:
    init_resp = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )
    if init_resp.status_code >= 400:
        raise RuntimeError(
            f"initAuth failed: HTTP {init_resp.status_code}: {init_resp.text}"
        )
    session_token = init_resp.headers.get("x-session-token")
    if not session_token:
        raise RuntimeError("initAuth did not return x-session-token")

    auth_resp = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={
            "method": "password",
            "credentials": {"password": TEST_USER_PASSWORD},
            "email": email,
        },
        timeout=timeout,
    )
    if auth_resp.status_code >= 400:
        raise RuntimeError(
            f"authenticate failed: HTTP {auth_resp.status_code}: {auth_resp.text}"
        )
    return str(auth_resp.json()["accessToken"])


def _create_oauth_app(base_url: str, access_token: str, timeout: int) -> tuple[str, str, str]:
    resp = requests.post(
        f"{base_url}/api/v1/oauth-clients",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "name": f"integration-2nd-user-{uuid.uuid4().hex[:8]}",
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": [
                "openid", "profile", "email", "org:read", "user:read",
                "kb:read", "conversation:read", "agent:read",
            ],
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"create OAuth app for 2nd user failed: "
            f"HTTP {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    app = data.get("app", {})
    return str(app["id"]), str(app["clientId"]), str(app["clientSecret"])


def _delete_user(pipeshub_client: PipeshubClient, user_id: str, timeout: int) -> None:
    try:
        pipeshub_client.request(
            "DELETE",
            f"{pipeshub_client.base_url}/api/v1/users/{user_id}",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to delete test user %s", user_id)


@pytest.fixture(scope="module")
def second_pipeshub_client(
    pipeshub_client: PipeshubClient,
) -> Iterator[PipeshubClient]:
    """Create a second PipeshubClient authenticated as a different (non-admin) user.

    The fixture:
      1. Creates a test user via the admin's client_credentials token
      2. Seeds a password in MongoDB for that user
      3. Logs in and creates an OAuth app as that user
      4. Sets CLIENT_ID/CLIENT_SECRET env vars to the second user's app
      5. Yields a new PipeshubClient that uses those credentials
      6. On teardown: deletes the OAuth app, user, and credentials,
         and restores original env vars
    """
    timeout = pipeshub_client.timeout_seconds
    org_id = pipeshub_client.org_id

    user = _create_test_user(pipeshub_client, timeout)
    user_id = user.get("_id") or user.get("id")
    email = user.get("email", "")

    _seed_password(org_id, user_id)
    try:
        access_token = _login(pipeshub_client.base_url, email, timeout)
        app_id, client_id, client_secret = _create_oauth_app(
            pipeshub_client.base_url, access_token, timeout,
        )

        # Save and override env vars for the second client
        saved_client_id = os.environ.get("CLIENT_ID")
        saved_client_secret = os.environ.get("CLIENT_SECRET")
        os.environ["CLIENT_ID"] = client_id
        os.environ["CLIENT_SECRET"] = client_secret

        try:
            second_client = PipeshubClient()
            second_client._invalidate_access_token()
            second_client._fetch_access_token()

            yield second_client
        finally:
            # Restore original env vars
            if saved_client_id is not None:
                os.environ["CLIENT_ID"] = saved_client_id
            else:
                os.environ.pop("CLIENT_ID", None)
            if saved_client_secret is not None:
                os.environ["CLIENT_SECRET"] = saved_client_secret
            else:
                os.environ.pop("CLIENT_SECRET", None)

            # Clean up the second user's OAuth app
            try:
                pipeshub_client.request(
                    "DELETE",
                    f"{pipeshub_client.base_url}/api/v1/oauth-clients/{app_id}",
                )
            except Exception:  # noqa: BLE001
                pass
    finally:
        _cleanup_credentials(org_id, user_id)
        _delete_user(pipeshub_client, user_id, timeout)
