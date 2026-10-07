"""Shared fixtures for the strict OpenAPI audit of /api/v1/knowledgeBase."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.kb_client import KBClient  # noqa: E402
from helper.kb_sharing import grant  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import SecondUser, second_user  # noqa: E402, F401 - fixture

from knowledge_base_audit_support import (  # noqa: E402
    UNRELATED_SCOPE,
    MakeKb,
    SeedRecord,
    WebRecordGroup,
    bearer,
    oauth_token_with_scopes,
    soft_delete_set_to,
    unique_name,
    upload_text_record,
    wait_for_record,
    web_record_group,
)


@pytest.fixture(scope="module")
def audit_kb_id(kb_client: KBClient) -> Iterator[str]:
    """A knowledge base owned by the admin, removed with everything in it after the module."""
    kb_id = kb_client.create_kb(unique_name("spec-audit-kb"))["id"]
    try:
        yield kb_id
    finally:
        kb_client.delete(f"/{kb_id}")


def _seed_into(kb_client: KBClient, kb_id: str) -> Iterator[SeedRecord]:
    created: list[str] = []

    def _seed(file_name: str | None = None, content: bytes | None = None) -> str:
        record_id = upload_text_record(kb_client, kb_id, file_name, content)
        created.append(record_id)
        wait_for_record(kb_client, record_id)
        return record_id

    try:
        yield _seed
    finally:
        for record_id in created:
            kb_client.delete(f"/record/{record_id}")


@pytest.fixture
def make_kb(
    kb_client: KBClient,
    pipeshub_client: PipeshubClient,
    second_user: SecondUser,  # noqa: F811 - the fixture imported above
) -> Iterator[MakeKb]:
    """Factory: a new knowledge base owned by the admin, deleted after the test.

    ``make_kb(member_role=None)`` returns its id; with a role, the member is granted it first.
    """
    created: list[str] = []

    def _make(member_role: str | None = None) -> str:
        kb_id = kb_client.create_kb(unique_name("spec-audit-kb"))["id"]
        created.append(kb_id)
        if member_role:
            grant(pipeshub_client, kb_id, user_ids=[second_user.user_id], role=member_role)
        return kb_id

    try:
        yield _make
    finally:
        for kb_id in created:
            kb_client.delete(f"/{kb_id}")


@pytest.fixture
def seed_record(kb_client: KBClient, audit_kb_id: str) -> Iterator[SeedRecord]:
    """Factory: upload one text file into ``audit_kb_id`` and return its record id.

    ``seed_record(file_name=None, content=None)``. Every record is deleted on
    teardown, including ones the test already deleted or restored.
    """
    yield from _seed_into(kb_client, audit_kb_id)


@pytest.fixture(scope="module")
def shared_kb_id(
    kb_client: KBClient,
    pipeshub_client: PipeshubClient,
    second_user: SecondUser,  # noqa: F811 - the fixture imported above
) -> Iterator[str]:
    """A knowledge base owned by the admin on which the member is a READER."""
    kb_id = kb_client.create_kb(unique_name("spec-audit-shared"))["id"]
    try:
        grant(pipeshub_client, kb_id, user_ids=[second_user.user_id], role="READER")
        yield kb_id
    finally:
        kb_client.delete(f"/{kb_id}")


@pytest.fixture
def seed_shared_record(kb_client: KBClient, shared_kb_id: str) -> Iterator[SeedRecord]:
    """``seed_record`` for ``shared_kb_id``: the member can read these records, not change them."""
    yield from _seed_into(kb_client, shared_kb_id)


@pytest.fixture(scope="session")
def unscoped_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Headers of an OAuth token for the admin's org that carries no knowledge base scope."""
    with oauth_token_with_scopes(
        pipeshub_client.base_url, [UNRELATED_SCOPE], pipeshub_client.timeout_seconds
    ) as token:
        yield bearer(token)


@pytest.fixture
def trash_on(pipeshub_client: PipeshubClient) -> Iterator[None]:
    """The trash ("Move Deleted Records to the Trash" in Labs) is on for this test only."""
    with soft_delete_set_to(pipeshub_client, True):
        yield


@pytest.fixture
def trash_off(pipeshub_client: PipeshubClient) -> Iterator[None]:
    """The trash ("Move Deleted Records to the Trash" in Labs) is off for this test only."""
    with soft_delete_set_to(pipeshub_client, False):
        yield


@pytest.fixture(scope="module")
def synced_record_group(kb_client: KBClient, pipeshub_client: PipeshubClient) -> Iterator[WebRecordGroup]:
    """A record group of an org-wide Web connector, with the one page it synced."""
    with web_record_group(pipeshub_client, kb_client) as group:
        yield group
