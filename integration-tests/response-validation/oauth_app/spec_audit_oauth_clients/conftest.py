"""Shared fixtures for the strict OpenAPI audit of /api/v1/oauth-clients."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.http.session_client import SessionClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from oauth_clients_audit_support import (  # noqa: E402
    SERVICE_ACCOUNTS_BASE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    SeedServiceAccount,
)


@pytest.fixture(scope="session")
def oauth_clients_client(user_session_client: SessionClient) -> OAuthClientsAuditClient:
    """The shared admin in person: this router refuses OAuth and personal access tokens."""
    return OAuthClientsAuditClient(user_session_client)


@pytest.fixture
def seed_oauth_app(oauth_clients_client: OAuthClientsAuditClient) -> Iterator[SeedOAuthApp]:
    """Factory: create one client_credentials app owned by the admin; deleted on teardown.

    ``seed_oauth_app(**overrides)`` returns the ``app`` object of the create response.
    """
    created: list[str] = []

    def _seed(**overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": f"spec-audit {uuid.uuid4().hex[:8]}",
            "allowedScopes": ["openid", "profile"],
            "allowedGrantTypes": ["client_credentials"],
            **overrides,
        }
        resp = oauth_clients_client.create_app(**body)
        assert resp.status_code == 201, f"seeding an OAuth app failed: {resp.status_code} {resp.text[:300]}"
        app: dict[str, Any] = resp.json()["app"]
        created.append(app["id"])
        return app

    try:
        yield _seed
    finally:
        for app_id in created:
            oauth_clients_client.delete_app(app_id)


@pytest.fixture
def seed_service_account(user_session_client: SessionClient) -> Iterator[SeedServiceAccount]:
    """Factory: create one service account in the org; deleted on teardown.

    ``seed_service_account(disabled=False)`` returns the account view (``id``, ``slug``, ...).
    """
    created: list[str] = []

    def _seed(disabled: bool = False) -> dict[str, Any]:
        slug = f"spec-audit-{uuid.uuid4().hex[:10]}"
        resp = user_session_client.request(
            "POST",
            SERVICE_ACCOUNTS_BASE,
            json={"slug": slug, "fullName": f"Spec audit {slug}"},
        )
        assert resp.status_code == 201, f"seeding a service account failed: {resp.status_code} {resp.text[:300]}"
        account: dict[str, Any] = resp.json()
        created.append(account["id"])
        if disabled:
            resp = user_session_client.request(
                "PATCH", f"{SERVICE_ACCOUNTS_BASE}/{account['id']}", json={"isDisabled": True}
            )
            assert resp.status_code == 200, f"disabling the service account failed: {resp.status_code} {resp.text[:300]}"
            account = resp.json()
        return account

    try:
        yield _seed
    finally:
        for account_id in created:
            user_session_client.request("DELETE", f"{SERVICE_ACCOUNTS_BASE}/{account_id}")
