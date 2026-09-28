"""
Helpers for creating a second PipeshubClient as a different (non-admin) user.

Creates a test user, seeds a password in MongoDB, logs in as that user, then
yields a client that uses the login session.  All resources are cleaned up on
teardown.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Iterator

import bcrypt
import pytest
import requests
from pymongo import MongoClient

from config import MONGO_DB_NAME, MONGO_URI, TEST_USER_PASSWORD
from pipeshub_client import PipeshubClient, SessionPipeshubClient

logger = logging.getLogger("second-user-auth")


def _random_email() -> str:
    uid = uuid.uuid4().hex[:12]
    return f"integration-test-{uid}@test-pipeshub.com"


def _create_test_user(pipeshub_client: PipeshubClient, timeout: int) -> dict:
    email = _random_email()
    full_name = f"Integration Test {uuid.uuid4().hex[:8]}"
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/users",
        headers=pipeshub_client._headers(),
        json={"fullName": full_name, "email": email, "role": "member"},
        timeout=timeout,
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


def _delete_user(pipeshub_client: PipeshubClient, user_id: str, timeout: int) -> None:
    try:
        requests.delete(
            f"{pipeshub_client.base_url}/api/v1/users/{user_id}",
            headers=pipeshub_client._headers(),
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to delete test user %s", user_id)


@pytest.fixture(scope="module")
def second_pipeshub_client(
    pipeshub_client: PipeshubClient,
) -> Iterator[PipeshubClient]:
    """Create a second PipeshubClient authenticated as a different (non-admin) user.

    The client uses a password-login session, not an OAuth token: the OAuth app
    routes this user is tested against reject OAuth and personal access tokens.

    The fixture:
      1. Creates a test user via the admin's client_credentials token
      2. Seeds a password in MongoDB for that user
      3. Logs in as that user and yields a client using the session token
      4. On teardown: deletes the user and credentials
    """
    timeout = pipeshub_client.timeout_seconds
    org_id = pipeshub_client.org_id

    user = _create_test_user(pipeshub_client, timeout)
    user_id = user.get("_id") or user.get("id")
    email = user.get("email", "")

    _seed_password(org_id, user_id)
    try:
        yield SessionPipeshubClient(
            session_token=_login(pipeshub_client.base_url, email, timeout)
        )
    finally:
        _cleanup_credentials(org_id, user_id)
        _delete_user(pipeshub_client, user_id, timeout)
