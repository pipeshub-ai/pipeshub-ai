"""Shared fixtures for the audit of behaviours the Node app applies to every route."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

_INTEGRATION_ROOT = Path(__file__).resolve().parents[2]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402

from global_audit_support import forget_access_token  # noqa: E402


@pytest.fixture(scope="session")
def no_scope_token(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """A client-credentials token of the suite's own OAuth client that carries no scope at all.

    The token endpoint drops `openid` for a client-credentials grant and grants the empty set.
    """
    resp = requests.post(
        f"{pipeshub_client.base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": "openid",
        },
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 200, resp.text[:300]
    assert resp.json().get("scope") == "", f"expected a token with no scope, got {resp.json().get('scope')!r}"
    token = str(resp.json()["access_token"])
    try:
        yield token
    finally:
        forget_access_token(token)
