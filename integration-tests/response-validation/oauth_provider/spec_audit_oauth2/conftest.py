"""Shared fixtures for the strict OpenAPI audit of /api/v1/oauth2."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.http.session_client import SessionClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from oauth2_audit_support import (  # noqa: E402
    FIRST_PARTY_DEVICE_CLIENT_ID,
    DeviceGrant,
    OAuth2Client,
    StartDeviceGrant,
    delete_device_grants,
)


@pytest.fixture(scope="session")
def oauth2_client(pipeshub_client: PipeshubClient) -> OAuth2Client:
    """Sends the admin's client-credentials token: the bearer token session-only routes refuse."""
    return OAuth2Client(pipeshub_client)


@pytest.fixture(scope="session")
def oauth2_session_client(user_session_client: SessionClient) -> OAuth2Client:
    """Sends the admin's password-login session JWT, which /device/verify and /device/consent need."""
    return OAuth2Client(user_session_client)


@pytest.fixture
def start_device_grant(oauth2_client: OAuth2Client) -> Iterator[StartDeviceGrant]:
    """Factory: open one pending device authorization; every one is removed on teardown.

    ``start_device_grant(client_id=FIRST_PARTY_DEVICE_CLIENT_ID, **body)`` posts to
    /device_authorization and fails the test unless that answers 200.
    """
    user_codes: list[str] = []

    def _start(
        client_id: str = FIRST_PARTY_DEVICE_CLIENT_ID, **body: Any
    ) -> DeviceGrant:
        resp = oauth2_client.device_authorization(client_id=client_id, **body)
        if resp.status_code != 200:
            pytest.fail(
                f"device_authorization did not start a grant: {resp.status_code} {resp.text[:300]}"
            )
        payload = resp.json()
        user_codes.append(payload["user_code"])
        return DeviceGrant(
            device_code=payload["device_code"],
            user_code=payload["user_code"],
            response=resp,
        )

    try:
        yield _start
    finally:
        delete_device_grants(user_codes)
