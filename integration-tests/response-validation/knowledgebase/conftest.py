from __future__ import annotations

import io
import mimetypes
import sys
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypedDict
from urllib.parse import unquote, urlparse
from uuid import uuid4

import pytest
import requests

from pipeshub_client import PipeshubClient

_IT_ROOT = Path(__file__).resolve().parents[2]
if str(_IT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IT_ROOT))

from messaging.test_e2e_record_pipeline import (  # noqa: E402
    _extract_record_id,
    poll_until,
)

_ASANA_PDF_BLOB_URL = (
    "https://github.com/pipeshub-ai/integration-test/blob/main/"
    "sample-data/entities/enterprise-search/"
    "Asana%20Disaster%20Recovery%20Summary%20Report%20(2023-08).pdf"
)

_KB_COUNT = 10
_RECORD_COUNT = 6
_PERMISSION_TEST_USER_COUNT = 4
_RECORDS_POLL_TIMEOUT_SEC = 180
_RECORDS_POLL_INTERVAL_SEC = 3
_GRAPH_USER_POLL_TIMEOUT_SEC = 60
_GRAPH_USER_POLL_INTERVAL_SEC = 3
_CLOCK_DRIFT_BUFFER_MS = 10_000


def _github_blob_to_raw(blob_url: str) -> str:
    parsed = urlparse(blob_url)
    if parsed.netloc == "raw.githubusercontent.com":
        return blob_url
    if parsed.netloc != "github.com" or "/blob/" not in parsed.path:
        raise ValueError(f"Not a GitHub blob URL: {blob_url}")
    new_path = parsed.path.replace("/blob/", "/", 1)
    return f"https://raw.githubusercontent.com{new_path}"


def _fetch_url_bytes(
    raw_url: str, preferred_name: str | None = None,
) -> tuple[bytes, str, str]:
    u = urlparse(raw_url.strip())
    if u.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs supported, got {u.scheme!r}")

    resp = requests.get(raw_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    buffer = resp.content

    fallback = unquote(u.path.rsplit("/", 1)[-1]) or "file"
    originalname = (
        (preferred_name or fallback).replace("/", "").replace("\\", "")[:255]
        or "file"
    )

    mimetype, _ = mimetypes.guess_type(originalname)
    if not mimetype:
        ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        mimetype = ct or "application/octet-stream"

    return buffer, originalname, mimetype


class TenKnowledgeBases(TypedDict):
    prefix: str
    ids: list[str]
    names: list[str]


class SixKbRecords(TypedDict):
    prefix: str
    kb_id: str
    record_ids: list[str]
    record_names: list[str]
    record_display_names: list[str]
    date_from_ms: int
    date_to_ms: int


def _wait_for_graph_user_by_email(
    client: PipeshubClient, email: str
) -> dict[str, object]:
    """Poll users/graph/list until the user exists in the graph (Kafka entity sync)."""
    email_lower = email.lower()
    search_term = email.split("@", 1)[0]

    def _user_in_graph() -> dict[str, object] | None:
        resp = requests.get(
            f"{client.base_url}/api/v1/users/graph/list",
            headers=client._headers(),
            params={"search": search_term, "limit": "50"},
            timeout=client.timeout_seconds,
        )
        if resp.status_code != 200:
            return None
        for user in resp.json().get("users") or []:
            if str(user.get("email") or "").lower() == email_lower:
                return user
        return None

    return poll_until(
        _user_in_graph,
        timeout=_GRAPH_USER_POLL_TIMEOUT_SEC,
        interval=_GRAPH_USER_POLL_INTERVAL_SEC,
        description=f"graph user for email {email!r}",
    )


def _create_one_permission_test_user(client: PipeshubClient) -> dict[str, object]:
    unique = uuid4().hex[:8]
    resp = requests.post(
        f"{client.base_url}/api/v1/users",
        headers=client._headers(),
        json={
            "fullName": f"RV KB Permissions {unique}",
            "email": f"rv-kb-perms-{unique}@test-pipeshub.com",
        },
        timeout=client.timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()


def _delete_permission_test_user(client: PipeshubClient, user_id: str) -> None:
    try:
        requests.delete(
            f"{client.base_url}/api/v1/users/{user_id}",
            headers=client._headers(),
            timeout=client.timeout_seconds,
        )
    except requests.RequestException:
        pass


def _create_one_permission_test_team(client: PipeshubClient) -> dict[str, object]:
    unique = uuid4().hex[:8]
    resp = requests.post(
        f"{client.base_url}/api/v1/userGroups",
        headers=client._headers(),
        json={"name": f"rv-kb-perms-team-{unique}", "type": "custom"},
        timeout=client.timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()


def _delete_permission_test_team(client: PipeshubClient, team_id: str) -> None:
    try:
        requests.delete(
            f"{client.base_url}/api/v1/userGroups/{team_id}",
            headers=client._headers(),
            timeout=client.timeout_seconds,
        )
    except requests.RequestException:
        pass


@pytest.fixture
def new_team(pipeshub_client: PipeshubClient) -> Generator[dict[str, object], None, None]:
    """Disposable team (full createUserGroup response body) for permission tests."""
    client = pipeshub_client
    client._ensure_access_token()
    team = _create_one_permission_test_team(client)
    try:
        yield team
    finally:
        team_id = str(team.get("_id") or team.get("id") or "")
        if team_id:
            _delete_permission_test_team(client, team_id)


@pytest.fixture(scope="module")
def four_new_users(
    pipeshub_client: PipeshubClient,
) -> Generator[list[dict[str, object]], None, None]:
    """Four disposable users (full createUser response bodies) for permission tests."""
    client = pipeshub_client
    client._ensure_access_token()
    users: list[dict[str, object]] = []
    for _ in range(_PERMISSION_TEST_USER_COUNT):
        user = _create_one_permission_test_user(client)
        email = str(user.get("email") or "")
        assert email, f"createUser response missing email: {user}"
        graph_user = _wait_for_graph_user_by_email(client, email)
        graph_id = graph_user.get("id")
        assert graph_id, f"graph user missing id for {email}: {graph_user}"
        user["graphId"] = graph_id
        users.append(user)
    try:
        yield users
    finally:
        for user in users:
            uid = str(user.get("_id") or user.get("id") or "")
            if uid:
                _delete_permission_test_user(client, uid)


@pytest.fixture
def ten_knowledge_bases(pipeshub_client: PipeshubClient) -> Generator[TenKnowledgeBases, None, None]:
    client = pipeshub_client
    client._ensure_access_token()
    url = f"{client.base_url}/api/v1/knowledgeBase/"
    headers = {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
    }
    timeout = client.timeout_seconds
    prefix = f"rv-list-{uuid4().hex[:8]}"
    names = [f"{prefix}-{i:02d}" for i in range(_KB_COUNT)]
    kb_ids: list[str] = []

    def _create_one(name: str) -> str:
        resp = requests.post(
            url,
            headers=headers,
            json={"kbName": name},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    with ThreadPoolExecutor(max_workers=_KB_COUNT) as pool:
        kb_ids = list(pool.map(_create_one, names))

    payload: TenKnowledgeBases = {"prefix": prefix, "ids": kb_ids, "names": names}
    try:
        yield payload
    finally:

        def _delete_one(kb_id: str) -> None:
            try:
                requests.delete(f"{url}{kb_id}", headers=headers, timeout=timeout)
            except requests.RequestException:
                pass

        with ThreadPoolExecutor(max_workers=_KB_COUNT) as pool:
            list(pool.map(_delete_one, kb_ids))


@pytest.fixture(scope="module")
def six_kb_records(pipeshub_client: PipeshubClient) -> Generator[SixKbRecords, None, None]:
    client = pipeshub_client
    client._ensure_access_token()
    kb_url = f"{client.base_url}/api/v1/knowledgeBase/"
    json_headers = {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type": "application/json",
    }
    timeout = client.timeout_seconds

    raw_url = _github_blob_to_raw(_ASANA_PDF_BLOB_URL)
    pdf_buffer, _, pdf_mimetype = _fetch_url_bytes(raw_url)

    prefix = f"rv-records-{uuid4().hex[:8]}"
    record_names = [f"{prefix}-{i:02d}.pdf" for i in range(1, _RECORD_COUNT + 1)]
    record_display_names = [name.removesuffix(".pdf") for name in record_names]

    create_resp = requests.post(
        kb_url,
        headers=json_headers,
        json={"kbName": f"rv-records-kb-{uuid4().hex[:8]}"},
        timeout=timeout,
    )
    create_resp.raise_for_status()
    kb_id = create_resp.json()["id"]

    record_ids: list[str] = []
    date_from_ms = max(0, int(time.time() * 1000) - _CLOCK_DRIFT_BUFFER_MS)

    try:
        upload_headers = {"Authorization": f"Bearer {client._access_token}"}
        upload_url = f"{kb_url}{kb_id}/upload"

        def _upload_one(record_name: str) -> str:
            files = [
                ("files", (record_name, io.BytesIO(pdf_buffer), pdf_mimetype)),
            ]
            upload_resp = requests.post(
                upload_url,
                headers=upload_headers,
                files=files,
                timeout=timeout,
            )
            upload_resp.raise_for_status()
            return _extract_record_id(upload_resp.json())

        with ThreadPoolExecutor(max_workers=_RECORD_COUNT) as pool:
            record_ids = list(pool.map(_upload_one, record_names))

        date_to_ms = int(time.time() * 1000)

        def _records_visible() -> bool:
            ready = 0
            for record_id in record_ids:
                resp = requests.get(
                    f"{kb_url}record/{record_id}",
                    headers=json_headers,
                    timeout=timeout,
                )
                if resp.status_code != 200:
                    continue
                body = resp.json()
                record = body.get("record") or body
                record_name = str(record.get("recordName") or "")
                if prefix in record_name:
                    ready += 1
            return ready >= _RECORD_COUNT

        poll_until(
            _records_visible,
            timeout=_RECORDS_POLL_TIMEOUT_SEC,
            interval=_RECORDS_POLL_INTERVAL_SEC,
            description=f"at least {_RECORD_COUNT} uploaded records matching {prefix}",
        )

        payload: SixKbRecords = {
            "prefix": prefix,
            "kb_id": kb_id,
            "record_ids": record_ids,
            "record_names": record_names,
            "record_display_names": record_display_names,
            "date_from_ms": date_from_ms,
            "date_to_ms": date_to_ms,
        }
        yield payload
    finally:
        try:
            requests.delete(
                f"{kb_url}{kb_id}",
                headers=json_headers,
                timeout=timeout,
            )
        except requests.RequestException:
            pass
