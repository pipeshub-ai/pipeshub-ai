"""Shared fixtures for the strict OpenAPI audit of /api/v1/service-accounts."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from service_accounts_audit_support import (  # noqa: E402
    SeedServiceAccount,
    ServiceAccountsClient,
    TrackServiceAccount,
    create_body,
)

logger = logging.getLogger("spec-audit-service-accounts")


@pytest.fixture(scope="session")
def service_accounts_client(pipeshub_client: PipeshubClient) -> ServiceAccountsClient:
    return ServiceAccountsClient(pipeshub_client)


@pytest.fixture
def track_service_account(
    service_accounts_client: ServiceAccountsClient,
) -> Iterator[TrackServiceAccount]:
    """Register the id of an account a test created itself; each is deleted on teardown.

    A 404 on teardown is fine: the test already deleted it.
    """
    created: list[str] = []
    try:
        yield created.append
    finally:
        for account_id in created:
            resp = service_accounts_client.remove(account_id)
            if resp.status_code not in (204, 404):
                logger.warning(
                    "could not delete service account %s: %s %s",
                    account_id, resp.status_code, resp.text[:300],
                )


@pytest.fixture
def seed_service_account(
    service_accounts_client: ServiceAccountsClient,
    track_service_account: TrackServiceAccount,
) -> SeedServiceAccount:
    """Factory: create one service account in the org; deleted on teardown.

    ``seed_service_account(disabled=False, **body_overrides)`` returns the account view
    (``id``, ``slug``, ``fullName``, ``email``, ``isDisabled``, ...).
    """

    def _seed(disabled: bool = False, **overrides: Any) -> dict[str, Any]:
        resp = service_accounts_client.create(json=create_body(**overrides))
        assert resp.status_code == 201, f"seeding a service account failed: {resp.status_code} {resp.text[:300]}"
        account: dict[str, Any] = resp.json()
        track_service_account(account["id"])
        if disabled:
            resp = service_accounts_client.update(account["id"], json={"isDisabled": True})
            assert resp.status_code == 200, f"disabling the service account failed: {resp.status_code} {resp.text[:300]}"
            account = resp.json()
        return account

    return _seed
