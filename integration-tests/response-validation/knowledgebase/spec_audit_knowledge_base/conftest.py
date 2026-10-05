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
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from knowledge_base_audit_support import (  # noqa: E402
    SeedRecord,
    unique_name,
    upload_text_record,
    wait_for_record,
)


@pytest.fixture(scope="module")
def audit_kb_id(kb_client: KBClient) -> Iterator[str]:
    """A knowledge base owned by the admin, removed with everything in it after the module.

    Its id is also a record group id (a knowledge base is a record group).
    """
    kb_id = kb_client.create_kb(unique_name("spec-audit-kb"))["id"]
    try:
        yield kb_id
    finally:
        kb_client.delete(f"/{kb_id}")


@pytest.fixture
def seed_record(kb_client: KBClient, audit_kb_id: str) -> Iterator[SeedRecord]:
    """Factory: upload one text file into ``audit_kb_id`` and return its record id.

    ``seed_record(file_name=None, content=None)``. Every record is deleted on
    teardown, including ones the test already deleted or restored.
    """
    created: list[str] = []

    def _seed(file_name: str | None = None, content: bytes | None = None) -> str:
        record_id = upload_text_record(kb_client, audit_kb_id, file_name, content)
        created.append(record_id)
        wait_for_record(kb_client, record_id)
        return record_id

    try:
        yield _seed
    finally:
        for record_id in created:
            kb_client.delete(f"/record/{record_id}")
