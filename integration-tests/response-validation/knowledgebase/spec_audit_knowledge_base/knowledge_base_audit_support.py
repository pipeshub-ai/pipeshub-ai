"""Constants and helpers for the strict OpenAPI audit of /api/v1/knowledgeBase."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

import requests

from helper.clients.kb_client import KBClient
from helper.local_auth import obtain_user_session_token
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

KB_BASE = "/api/v1/knowledgeBase"

# Record, knowledge base and record group ids are graph keys (UUIDs). The Node
# validators only require a non-empty string, so there is no "malformed" id the
# gateway rejects by shape: both of these reach the connector service.
MISSING_RECORD_ID = "00000000-0000-4000-8000-000000000000"
MISSING_RECORD_GROUP_ID = "00000000-0000-4000-8000-000000000001"
MALFORMED_ID = "not-a-graph-id"
# Decoded by Express to "a%b": guardPathParams refuses "%" in an id it pastes into a service URL.
UNSAFE_ID = "a%25b"
UNSAFE_ID_MESSAGE = "This address contains an ID that isn't valid"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

HTML_REFUSED_MESSAGE = "HTML tags, scripts, and XSS content are not allowed"

OAUTH_CLIENTS_PATH = "/api/v1/oauth-clients"
OAUTH_TOKEN_PATH = "/api/v1/oauth2/token"
# A scope none of the knowledge base routes accept.
UNRELATED_SCOPE = "org:read"

PLATFORM_SETTINGS_PATH = "/api/v1/configurationManager/platform/settings"
SOFT_DELETE_FLAG = "ENABLE_SOFT_DELETE"
SOFT_DELETE_OFF_MESSAGE = "Restoring deleted items is turned off in this workspace"

# MAX_RESTORE_RECORD_IDS in the Node validator and the connector service.
MAX_RESTORE_RECORD_IDS = 100

DEMO_STATUS_FIELDS = frozenset(
    {"hasDemo", "include", "chosen", "realData", "offForEveryone", "demoConnectorIds"}
)

RECORD_VISIBLE_TIMEOUT_SEC = 60.0
RECORD_VISIBLE_INTERVAL_SEC = 2.0

SeedRecord = Callable[..., str]


def unique_name(prefix: str = "spec-audit") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def upload_text_record(
    kb_client: KBClient, kb_id: str, file_name: str | None = None, content: bytes | None = None
) -> str:
    """Upload one small text file to a knowledge base and return its record id."""
    file_name = file_name or f"{unique_name()}.txt"
    uploaded = kb_client.upload_file(
        kb_id, file_name, content or f"Seeded by the knowledge base spec audit: {file_name}".encode()
    )
    record = uploaded["records"][0]
    record_id = record.get("recordId") or record.get("_key") or record.get("id")
    if not record_id:
        raise AssertionError(f"upload answered without a record id: {uploaded}")
    return str(record_id)


def wait_for_record(kb_client: KBClient, record_id: str) -> None:
    """Block until GET /record/:recordId answers 200; the upload stream can finish first."""
    deadline = time.monotonic() + RECORD_VISIBLE_TIMEOUT_SEC
    status = 0
    while time.monotonic() < deadline:
        status = kb_client.get(f"/record/{record_id}").status_code
        if status == 200:
            return
        time.sleep(RECORD_VISIBLE_INTERVAL_SEC)
    raise AssertionError(f"record {record_id} never became readable, last status {status}")


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a knowledge base route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = dict(user.headers)
    # requests must write the multipart boundary itself.
    if kwargs.get("files"):
        headers.pop("Content-Type", None)
    return requests.request(
        method, f"{user.base_url}{KB_BASE}{path}", headers=headers, **kwargs
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@contextmanager
def oauth_token_with_scopes(base_url: str, scopes: list[str], timeout: int = 60) -> Iterator[str]:
    """A client-credentials token limited to ``scopes``, from an OAuth app that is deleted on exit.

    The suite's own token carries every scope, and a session JWT is never scope-checked, so this is
    the only caller that can be refused for a missing scope.
    """
    admin = bearer(obtain_user_session_token(base_url, timeout))
    created = requests.post(
        f"{base_url}{OAUTH_CLIENTS_PATH}",
        headers=admin,
        json={
            "name": unique_name("spec-audit-kb-scope"),
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": scopes,
        },
        timeout=timeout,
    )
    assert created.status_code == 201, f"creating an OAuth app failed: {created.status_code} {created.text[:300]}"
    app = created.json()["app"]
    try:
        issued = requests.post(
            f"{base_url}{OAUTH_TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "client_id": app["clientId"],
                "client_secret": app["clientSecret"],
            },
            timeout=timeout,
        )
        assert issued.status_code == 200, f"token request failed: {issued.status_code}"
        yield issued.json()["access_token"]
    finally:
        requests.delete(f"{base_url}{OAUTH_CLIENTS_PATH}/{app['id']}", headers=admin, timeout=timeout)


def _write_soft_delete_flag(client: PipeshubClient, value: bool | None) -> bool | None:
    """Set the flag (``None`` removes it) and return what it was.

    Read and written in one go because the POST replaces every platform setting, and other suites
    change other flags while this runs.
    """
    current = client.request("GET", PLATFORM_SETTINGS_PATH)
    assert current.status_code == 200, f"reading platform settings: {current.status_code} {current.text[:300]}"
    settings = current.json()
    flags = dict(settings.get("featureFlags") or {})
    before = flags.get(SOFT_DELETE_FLAG)
    if before is value:
        return before
    if value is None:
        flags.pop(SOFT_DELETE_FLAG, None)
    else:
        flags[SOFT_DELETE_FLAG] = value
    saved = client.request(
        "POST",
        PLATFORM_SETTINGS_PATH,
        json={"fileUploadMaxSizeBytes": settings["fileUploadMaxSizeBytes"], "featureFlags": flags},
    )
    assert saved.status_code == 200, f"saving platform settings: {saved.status_code} {saved.text[:300]}"
    return before


@contextmanager
def soft_delete_set_to(client: PipeshubClient, enabled: bool) -> Iterator[None]:
    """Hold "Move Deleted Records to the Trash" at ``enabled`` for the block, then put it back.

    The flag is org-wide and the connector service reads it on every delete and restore, so keep
    the block short: other suites delete records while it is on.
    """
    before = _write_soft_delete_flag(client, enabled)
    try:
        yield
    finally:
        _write_soft_delete_flag(client, before)
