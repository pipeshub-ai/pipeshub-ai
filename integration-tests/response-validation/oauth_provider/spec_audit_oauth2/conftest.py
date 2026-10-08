"""Shared fixtures for the strict OpenAPI audit of /api/v1/oauth2."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.http.session_client import SessionClient  # noqa: E402
from helper.mcp_oauth import OAuthApp, create_oauth_app, delete_oauth_app  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import (  # noqa: E402
    create_second_user,
    delete_second_user,
    second_user,  # noqa: F401 - fixture
)

from oauth2_audit_support import (  # noqa: E402
    APP_GRANT_TYPES,
    FIRST_PARTY_DEVICE_CLIENT_ID,
    OIDC_SCOPES,
    DeviceGrant,
    OAuth2Client,
    StartDeviceGrant,
    create_public_app,
    delete_device_grants,
    insert_device_grant,
    random_user_code,
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


@pytest.fixture(scope="session")
def confidential_app(user_session_client: SessionClient) -> Iterator[OAuthApp]:
    """A confidential app with every grant /oauth-clients allows, the OIDC scopes and offline_access."""
    base_url, session_jwt = user_session_client.base_url, user_session_client.token
    app = create_oauth_app(
        base_url,
        session_jwt,
        f"spec-audit-oauth2-{uuid.uuid4().hex[:8]}",
        scopes=[*OIDC_SCOPES, "offline_access"],
        grant_types=APP_GRANT_TYPES,
    )
    try:
        yield app
    finally:
        delete_oauth_app(base_url, session_jwt, app.id)


@pytest.fixture(scope="session")
def public_app(user_session_client: SessionClient) -> Iterator[OAuthApp]:
    base_url, session_jwt = user_session_client.base_url, user_session_client.token
    app = create_public_app(base_url, session_jwt, OIDC_SCOPES)
    try:
        yield app
    finally:
        delete_oauth_app(base_url, session_jwt, app.id)


@pytest.fixture(scope="session")
def deleted_user_session_jwt(pipeshub_client: PipeshubClient) -> str:
    """The session JWT of a member whose account has since been deleted; it has not expired."""
    pipeshub_client._ensure_access_token()
    user = create_second_user(pipeshub_client)
    delete_second_user(pipeshub_client, user, strict=True)
    return user.token


InsertDeviceGrant = Callable[..., str]


@pytest.fixture
def inserted_device_grant() -> Iterator[InsertDeviceGrant]:
    """Factory: write a pending grant to Mongo and return its user_code; removed on teardown.

    ``inserted_device_grant(client_id=..., expires_in=...)``; a negative ``expires_in`` makes it expired.
    """
    user_codes: list[str] = []

    def _insert(**kwargs: Any) -> str:
        user_code = random_user_code()
        user_codes.append(user_code)
        insert_device_grant(user_code, **kwargs)
        return user_code

    try:
        yield _insert
    finally:
        delete_device_grants(user_codes)
