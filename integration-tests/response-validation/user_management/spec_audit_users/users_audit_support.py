"""Constants and helpers for the strict OpenAPI audit of /api/v1/users."""

from __future__ import annotations

import base64
import datetime
import os
import struct
import uuid
import zlib
from typing import Any, Callable

import jwt
import requests
from pymongo import MongoClient
from pymongo.collection import Collection

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

USERS_BASE = "/api/v1/users"
MISSING_USER_ID = "0123456789abcdef01234567"
MALFORMED_USER_ID = "not-an-object-id"

INVALID_BEARER = {"Authorization": "Bearer not-a-jwt"}

USER_LOOKUP_SCOPE = "user:lookup"
FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope no route of this router accepts.
OTHER_SCOPE = "storage:token"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

SeededUser = dict[str, Any]
SeedUser = Callable[..., SeededUser]
MintScopedToken = Callable[..., str]


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    secret: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 3600,
    **claims: Any,
) -> str:
    """Service token shaped like Node's createJwt ones; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, secret, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def request_with_token(
    client: PipeshubClient, token: str, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a users route with exactly this bearer token; path is relative to the router."""
    # auth=False so the helper neither overwrites the header nor retries a 401
    # with a refreshed admin token.
    return client.request(
        method, f"{USERS_BASE}{path}", auth=False, headers=bearer(token), **kwargs
    )


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a users route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{USERS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# safeParsePagination's MAX_PAGE: floor(Number.MAX_SAFE_INTEGER / 100).
MAX_LIST_PAGE = 90071992547409

DEMO_EMAIL_DOMAIN = "acme-demo.example"
STRONG_PASSWORD = "Spec#Audit1"

# A 1x1 red PNG.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def unique_email(domain: str = "test-pipeshub.com") -> str:
    return f"spec-audit-{uuid.uuid4().hex[:10]}@{domain}"


def png_bytes(width: int, height: int) -> bytes:
    """A grey-gradient PNG; large dimensions stay small as PNG but not as JPEG."""
    row = bytes([0]) + bytes((x * 255 // max(width - 1, 1)) for x in range(width))
    raw = row * height
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def put_display_picture(
    user: SecondUser, files: Any = None, **kwargs: Any
) -> requests.Response:
    """PUT /users/dp as the member, multipart when ``files`` is given."""
    headers = {"Authorization": f"Bearer {user.token}", **kwargs.pop("headers", {})}
    return requests.put(
        f"{user.base_url}{USERS_BASE}/dp",
        headers=headers,
        files=files,
        timeout=user.timeout,
        **kwargs,
    )


def credentials_collection(client: MongoClient) -> Collection:
    return client[MONGO_DB_NAME].userCredentials


def insert_blocked_credentials(org_id: str, user_id: str) -> Any:
    """A credential row as the login lockout leaves it; returns its _id for cleanup."""
    now = datetime.datetime.now(datetime.timezone.utc)
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        return credentials_collection(client).insert_one(
            {
                "userId": str(user_id),
                "orgId": str(org_id),
                "ipAddress": "127.0.0.1",
                "wrongCredentialCount": 5,
                "isBlocked": True,
                "forceNewPasswordGeneration": False,
                "isDeleted": False,
                "createdAt": now,
                "updatedAt": now,
            }
        ).inserted_id


def read_credentials(user_id: str) -> list[dict[str, Any]]:
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        return list(credentials_collection(client).find({"userId": str(user_id)}))


def delete_credentials(user_id: str) -> None:
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        credentials_collection(client).delete_many({"userId": str(user_id)})
