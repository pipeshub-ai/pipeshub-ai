"""
Helpers for creating a second PipeshubClient as a different (non-admin) user.

Creates a test user, seeds a password in MongoDB, authenticates, creates an
OAuth app as that user, then yields a PipeshubClient configured with those
second credentials.  All resources are cleaned up on teardown.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import bcrypt
import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient

from config import MONGO_DB_NAME, MONGO_URI, TEST_USER_PASSWORD
from pipeshub_client import PipeshubClient

logger = logging.getLogger("second-user-auth")

# Minimal scopes for streaming KB / connector records (require_scopes is OR).
_STREAM_OAUTH_SCOPES = [
    "openid",
    "profile",
    "email",
    "org:read",
    "user:read",
    "kb:read",
    "connector:read",
    "conversation:read",
    "agent:read",
]


def _mongo_db_candidates() -> list[str]:
    """DB names to try — Node may use ``es`` or ``enterprise-search``."""
    out: list[str] = []
    for name in (MONGO_DB_NAME, "enterprise-search", "es"):
        if name and name not in out:
            out.append(name)
    return out


def _random_email() -> str:
    uid = uuid.uuid4().hex[:12]
    return f"integration-test-{uid}@test-pipeshub.com"


def _create_test_user(pipeshub_client: PipeshubClient, timeout: int) -> dict:
    email = _random_email()
    full_name = f"Integration Test {uuid.uuid4().hex[:8]}"
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/users",
        headers=pipeshub_client._headers(),
        json={"fullName": full_name, "email": email},
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


def _user_object_id(user_id: str) -> ObjectId | str:
    if ObjectId.is_valid(user_id):
        return ObjectId(user_id)
    return user_id


def _resolve_mongo_db_name(user_id: str, org_id: str) -> str:
    """Pick the Mongo DB that holds this user (or their credentials)."""
    oid = _user_object_id(user_id)
    client = MongoClient(MONGO_URI)
    try:
        for db_name in _mongo_db_candidates():
            db = client[db_name]
            if db["users"].find_one({"_id": oid}):
                logger.info("Resolved Mongo DB %s via users._id=%s", db_name, user_id)
                return db_name
            if db["userCredentials"].find_one(
                {"userId": str(user_id), "orgId": str(org_id)}
            ):
                logger.info(
                    "Resolved Mongo DB %s via userCredentials userId=%s",
                    db_name,
                    user_id,
                )
                return db_name
    finally:
        client.close()

    fallback = _mongo_db_candidates()[0]
    logger.warning(
        "Could not locate user %s in Mongo candidates %s; using %s",
        user_id,
        _mongo_db_candidates(),
        fallback,
    )
    return fallback


@dataclass
class SeededPassword:
    """Handle to restore credentials after a temporary password seed."""

    db_name: str
    org_id: str
    user_id: str
    previous_doc: Optional[dict[str, Any]]
    inserted_new: bool


def seed_password(org_id: str, user_id: str) -> SeededPassword:
    """Upsert ``TEST_USER_PASSWORD`` for *user_id*; return restore handle.

    Writes into the Mongo DB that actually holds the user (``es`` or
    ``enterprise-search``). Existing credential docs are snapshotted so
    :func:`cleanup_credentials` can restore them instead of deleting a real
    user's password.
    """
    org_id = str(org_id)
    user_id = str(user_id)
    db_name = _resolve_mongo_db_name(user_id, org_id)
    hashed = bcrypt.hashpw(TEST_USER_PASSWORD.encode(), bcrypt.gensalt()).decode()
    now = datetime.datetime.now(datetime.timezone.utc)
    oid = _user_object_id(user_id)

    client = MongoClient(MONGO_URI)
    try:
        db = client[db_name]
        user_doc = db["users"].find_one({"_id": oid})
        if user_doc and user_doc.get("orgId") is not None:
            # Prefer the orgId stored on the user doc (auth lookup key).
            org_id = str(user_doc["orgId"])

        coll = db["userCredentials"]
        # Auth looks up { userId, orgId, isDeleted: false }; match both string forms.
        previous = coll.find_one(
            {
                "userId": user_id,
                "orgId": org_id,
            }
        )
        if previous is None:
            # Some older rows may store ObjectId-ish values inconsistently.
            previous = coll.find_one({"userId": user_id})
            if previous is not None and previous.get("orgId") is not None:
                org_id = str(previous["orgId"])

        previous_copy = dict(previous) if previous else None
        inserted_new = previous is None

        coll.update_one(
            {"userId": user_id, "orgId": org_id},
            {
                "$set": {
                    "hashedPassword": hashed,
                    "ipAddress": "127.0.0.1",
                    "wrongCredentialCount": 0,
                    "isBlocked": False,
                    "blockExpiresAt": None,
                    "forceNewPasswordGeneration": False,
                    "isDeleted": False,
                    "updatedAt": now,
                },
                "$setOnInsert": {
                    "userId": user_id,
                    "orgId": org_id,
                    "createdAt": now,
                },
            },
            upsert=True,
        )
        logger.info(
            "Seeded test password in %s.userCredentials for userId=%s orgId=%s "
            "(had_existing=%s)",
            db_name,
            user_id,
            org_id,
            previous is not None,
        )
        return SeededPassword(
            db_name=db_name,
            org_id=org_id,
            user_id=user_id,
            previous_doc=previous_copy,
            inserted_new=inserted_new,
        )
    finally:
        client.close()


def cleanup_credentials(
    org_id: str,
    user_id: str,
    seeded: SeededPassword | None = None,
) -> None:
    """Restore prior credentials, or delete a doc we inserted for an ephemeral user."""
    try:
        client = MongoClient(MONGO_URI)
        try:
            if seeded is not None:
                coll = client[seeded.db_name]["userCredentials"]
                if seeded.previous_doc is not None:
                    prev = dict(seeded.previous_doc)
                    prev_id = prev.pop("_id", None)
                    if prev_id is not None:
                        coll.replace_one({"_id": prev_id}, {**prev, "_id": prev_id})
                    else:
                        coll.replace_one(
                            {"userId": seeded.user_id, "orgId": seeded.org_id},
                            prev,
                            upsert=True,
                        )
                    logger.info(
                        "Restored prior userCredentials for userId=%s in %s",
                        seeded.user_id,
                        seeded.db_name,
                    )
                elif seeded.inserted_new:
                    coll.delete_one(
                        {"userId": seeded.user_id, "orgId": seeded.org_id}
                    )
                return

            # Legacy path: try all candidate DBs.
            for db_name in _mongo_db_candidates():
                client[db_name]["userCredentials"].delete_one(
                    {"userId": str(user_id), "orgId": str(org_id)}
                )
        finally:
            client.close()
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to clean up credentials for user %s; a temporary password may remain",
            user_id,
            exc_info=True,
        )


def login_with_test_password(base_url: str, email: str, timeout: int) -> str:
    """Password-login with ``TEST_USER_PASSWORD``; return access token."""
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


def create_oauth_app_for_user(
    base_url: str,
    access_token: str,
    timeout: int,
    *,
    name_prefix: str = "integration-2nd-user",
    scopes: list[str] | None = None,
) -> tuple[str, str, str]:
    """Create a client_credentials OAuth app; return (app_id, client_id, client_secret)."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth-clients",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "name": f"{name_prefix}-{uuid.uuid4().hex[:8]}",
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": list(scopes or _STREAM_OAUTH_SCOPES),
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"create OAuth app failed: HTTP {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    app = data.get("app", {})
    return str(app["id"]), str(app["clientId"]), str(app["clientSecret"])


def _delete_user(pipeshub_client: PipeshubClient, user_id: str, timeout: int) -> None:
    try:
        requests.delete(
            f"{pipeshub_client.base_url}/api/v1/users/{user_id}",
            headers=pipeshub_client._headers(),
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to delete test user %s", user_id)


@contextmanager
def pipeshub_client_as_user(
    admin_client: PipeshubClient,
    *,
    email: str,
    user_id: str,
    name_prefix: str = "integration-user-client",
    scopes: list[str] | None = None,
    cleanup_user: bool = False,
) -> Iterator[PipeshubClient]:
    """Yield a ``PipeshubClient`` authenticated as an existing org user.

    Seeds Mongo password for *user_id*, logs in, creates an OAuth app owned by
    that user, and builds a client with those credentials (without mutating
    process-wide ``CLIENT_ID``/``CLIENT_SECRET``). Does not delete the user
    unless ``cleanup_user=True``.
    """
    timeout = admin_client.timeout_seconds
    org_id = admin_client.org_id
    base_url = admin_client.base_url

    seed = seed_password(org_id, user_id)
    app_id: str | None = None
    try:
        access_token = login_with_test_password(base_url, email, timeout)
        app_id, client_id, client_secret = create_oauth_app_for_user(
            base_url,
            access_token,
            timeout,
            name_prefix=name_prefix,
            scopes=scopes,
        )

        user_client = PipeshubClient(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
        )
        user_client._invalidate_access_token()
        user_client._fetch_access_token()
        logger.info(
            "PipeshubClient as user %s (id=%s) ready for streaming",
            email,
            user_id,
        )
        yield user_client
    finally:
        if app_id:
            try:
                requests.delete(
                    f"{base_url}/api/v1/oauth-clients/{app_id}",
                    headers=admin_client._headers(),
                    timeout=timeout,
                )
            except Exception:  # noqa: BLE001
                logger.warning("Failed to delete OAuth app %s", app_id)

        cleanup_credentials(org_id, user_id, seeded=seed)
        if cleanup_user:
            _delete_user(admin_client, user_id, timeout)


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
    user = _create_test_user(pipeshub_client, pipeshub_client.timeout_seconds)
    user_id = str(user.get("_id") or user.get("id") or "")
    email = str(user.get("email") or "")
    if not user_id or not email:
        raise RuntimeError(f"createUser response missing id/email: {user}")

    with pipeshub_client_as_user(
        pipeshub_client,
        email=email,
        user_id=user_id,
        name_prefix="integration-2nd-user",
        cleanup_user=True,
    ) as second_client:
        yield second_client
