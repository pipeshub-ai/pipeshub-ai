"""Constants and helpers for the strict OpenAPI audit of /api/v1/knowledgeBase."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

import requests

from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser

KB_BASE = "/api/v1/knowledgeBase"

# Record, knowledge base and record group ids are graph keys (UUIDs). The Node
# validators only require a non-empty string, so there is no "malformed" id the
# gateway rejects by shape: both of these reach the connector service.
MISSING_RECORD_ID = "00000000-0000-4000-8000-000000000000"
MISSING_RECORD_GROUP_ID = "00000000-0000-4000-8000-000000000001"
MALFORMED_ID = "not-a-graph-id"

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
