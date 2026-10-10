"""Shared fixtures for the audit of behaviours the Node app applies to every route."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[2]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402

from global_audit_support import forget_access_token, mint_token  # noqa: E402


@pytest.fixture(scope="session")
def no_scope_token(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """A client-credentials token of the suite's own OAuth client that carries no scope at all."""
    token = mint_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds, "openid")
    try:
        yield token
    finally:
        forget_access_token(token)


@pytest.fixture(scope="session")
def connector_read_token(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """A client-credentials token of the suite's own OAuth client holding only `connector:read`."""
    token = mint_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds, "connector:read")
    try:
        yield token
    finally:
        forget_access_token(token)
