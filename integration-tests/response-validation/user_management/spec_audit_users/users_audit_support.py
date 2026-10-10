"""Constants and helpers for the strict OpenAPI audit of /api/v1/users."""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import os
import random
import struct
import time
import uuid
import zlib
from typing import Any, Callable

import jwt
import requests
from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection

from helper import mailpit
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


def noisy_png(width: int, height: int) -> bytes:
    """A greyscale PNG of repeating random rows: tiny as PNG, hundreds of KB as JPEG.

    ``width`` must be a multiple of 256.
    """
    rnd = random.Random(7)
    tiles = [bytes(rnd.randrange(256) for _ in range(256)) for _ in range(4)]
    raw = b"".join(b"\x00" + tiles[y % 4] * (width // 256) for y in range(height))

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


def image_bytes(fmt: str, size: tuple[int, int] = (2, 2)) -> bytes:
    """A tiny solid image encoded as ``fmt`` (a Pillow format name such as PNG or WEBP)."""
    from PIL import Image  # noqa: PLC0415 - only the display-picture tests need Pillow

    out = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(out, fmt)
    return out.getvalue()


def display_pictures_collection(client: MongoClient) -> Collection:
    return client[MONGO_DB_NAME]["user-dps"]


def read_display_pictures(user_id: str) -> list[dict[str, Any]]:
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        return list(display_pictures_collection(client).find({"userId": ObjectId(user_id)}))


def delete_display_pictures(user_id: str) -> None:
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        display_pictures_collection(client).delete_many({"userId": ObjectId(user_id)})


def display_picture_request(
    user: SecondUser, method: str, **kwargs: Any
) -> requests.Response:
    """Call /users/dp as ``user`` with only the bearer header, so ``files=`` sets the multipart type."""
    headers = {"Authorization": f"Bearer {user.token}", **kwargs.pop("headers", {})}
    return requests.request(
        method,
        f"{user.base_url}{USERS_BASE}/dp",
        headers=headers,
        timeout=user.timeout,
        **kwargs,
    )


# A real OAuth scope that grants nothing on this router.
NARROW_SCOPE = "team:read"


def mint_narrow_scope_token(base_url: str, timeout: int = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to NARROW_SCOPE."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": NARROW_SCOPE,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {NARROW_SCOPE} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == NARROW_SCOPE, f"asked for {NARROW_SCOPE!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})


def users_with_emails(emails: list[str]) -> list[dict[str, Any]]:
    """Every user document, deleted or not, holding one of these addresses."""
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        return list(client[MONGO_DB_NAME].users.find({"email": {"$in": [e.lower() for e in emails]}}))


def mail_ids_to(addresses: list[str]) -> set[str]:
    return set().union(*(mailpit.message_ids(a) for a in addresses)) if addresses else set()


def wait_for_mail_to_settle(addresses: list[str], quiet: float = 4, limit: float = 30) -> None:
    """Return once no new message to ``addresses`` has arrived for ``quiet`` seconds.

    Invitation mail goes through the message broker, so it can land after the
    request that queued it has returned.
    """
    deadline = time.monotonic() + limit
    seen = mail_ids_to(addresses)
    quiet_since = time.monotonic()
    while time.monotonic() < deadline and time.monotonic() - quiet_since < quiet:
        time.sleep(1)
        now = mail_ids_to(addresses)
        if now != seen:
            seen, quiet_since = now, time.monotonic()


def forget_mail(addresses: list[str]) -> None:
    """Delete from Mailpit every message sent to these addresses (they are this suite's own)."""
    ids = sorted(mail_ids_to(addresses))
    if ids:
        resp = requests.delete(
            f"{mailpit.mailpit_url()}/api/v1/messages", json={"IDs": ids}, timeout=10
        )
        assert resp.status_code == 200, f"Mailpit delete answered {resp.status_code}"


def delete_invite_notifications(user_id: str) -> int:
    """Remove the bulk-invite notifications addressed to ``user_id``; returns how many."""
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        return client[MONGO_DB_NAME]["notifications"].delete_many(
            {"assignedTo": ObjectId(user_id), "type": "user.bulkInvite"}
        ).deleted_count


def csv_file(*cells: str) -> dict[str, tuple[str, bytes, str]]:
    """A ``files=`` mapping holding a one-column CSV."""
    return {"file": ("invite.csv", "\n".join(cells).encode() + b"\n", "text/csv")}


# The controller's own address check (users.controller.ts EMAIL_REGEX), applied after zod.
INVITE_EMAIL_PATTERN = r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$"
