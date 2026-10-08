"""Shared fixtures for the strict OpenAPI audit of /api/v1/health."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402

from health_audit_support import HealthClient  # noqa: E402


@pytest.fixture(scope="session")
def health_client(pipeshub_client: PipeshubClient) -> HealthClient:
    return HealthClient(pipeshub_client)
