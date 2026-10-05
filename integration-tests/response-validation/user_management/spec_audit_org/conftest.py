"""Shared fixtures for the strict OpenAPI audit of /api/v1/org."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.org_client import OrgClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from org_audit_support import current_org_id  # noqa: E402


@pytest.fixture
def org_intact(org_client: OrgClient) -> Iterator[str]:
    """Yield the shared org's id and fail on teardown if the test replaced or deleted it."""
    org_id = current_org_id(org_client)
    yield org_id
    assert current_org_id(org_client) == org_id, "the shared org changed during the test"
