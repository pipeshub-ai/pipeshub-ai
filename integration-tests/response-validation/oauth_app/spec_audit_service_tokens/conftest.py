"""Shared fixtures for the strict OpenAPI audit of /api/v1/service-tokens."""

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

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from service_tokens_audit_support import (  # noqa: E402
    SERVICE_ACCOUNTS_BASE,
    SeedServiceAccount,
    SeedServiceToken,
    ServiceTokensClient,
    create_token_body,
)


@pytest.fixture(scope="session")
def service_tokens_client(pipeshub_client: PipeshubClient) -> ServiceTokensClient:
    return ServiceTokensClient(pipeshub_client)


@pytest.fixture
def seed_service_account(pipeshub_client: PipeshubClient) -> Iterator[SeedServiceAccount]:
    """Factory: create one service account in the org; deleted on teardown.

    ``seed_service_account(disabled=False)`` returns the account view (``id``, ``slug``, ...).
    Deleting the account also revokes every token it still holds.
    """
    created: list[str] = []

    def _seed(disabled: bool = False) -> dict[str, Any]:
        slug = f"spec-audit-{uuid.uuid4().hex[:10]}"
        resp = pipeshub_client.request(
            "POST",
            SERVICE_ACCOUNTS_BASE,
            json={"slug": slug, "fullName": f"Spec audit {slug}"},
        )
        assert resp.status_code == 201, f"seeding a service account failed: {resp.status_code} {resp.text[:300]}"
        account: dict[str, Any] = resp.json()
        created.append(account["id"])
        if disabled:
            resp = pipeshub_client.request(
                "PATCH", f"{SERVICE_ACCOUNTS_BASE}/{account['id']}", json={"isDisabled": True}
            )
            assert resp.status_code == 200, f"disabling the service account failed: {resp.status_code} {resp.text[:300]}"
            account = resp.json()
        return account

    try:
        yield _seed
    finally:
        for account_id in created:
            pipeshub_client.request("DELETE", f"{SERVICE_ACCOUNTS_BASE}/{account_id}")


@pytest.fixture
def seed_service_token(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> Iterator[SeedServiceToken]:
    """Factory: mint one token for a service account; revoked on teardown.

    ``seed_service_token(service_account_id=None, **overrides)`` returns the ``token``
    object of the create response. With no id it seeds a fresh enabled account and
    the returned ``serviceAccountId`` names it.
    """
    created: list[tuple[str, str]] = []

    def _seed(service_account_id: str | None = None, **overrides: Any) -> dict[str, Any]:
        account_id = service_account_id or seed_service_account()["id"]
        resp = service_tokens_client.create_token(**create_token_body(account_id, **overrides))
        assert resp.status_code == 201, f"seeding a service token failed: {resp.status_code} {resp.text[:300]}"
        token: dict[str, Any] = resp.json()["token"]
        created.append((token["id"], account_id))
        return token

    try:
        yield _seed
    finally:
        for token_id, account_id in created:
            service_tokens_client.revoke_token(token_id, account_id)
