"""Shared fixtures for the strict OpenAPI audit of /api/v1/personal-access-tokens."""

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
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import SecondUser, second_user  # noqa: E402, F401 - fixture

from personal_access_tokens_audit_support import (  # noqa: E402
    MintPat,
    PatsClient,
    pat_name,
    request_as,
)


@pytest.fixture(scope="session")
def pats_client(user_session_client: SessionClient) -> PatsClient:
    """The org admin in person: the router refuses anything but a login session JWT."""
    return PatsClient(user_session_client)


@pytest.fixture(scope="session")
def oauth_pats_client(pipeshub_client: PipeshubClient) -> PatsClient:
    """The same admin behind an OAuth client-credentials token, which requireSessionAuth refuses."""
    return PatsClient(pipeshub_client)


@pytest.fixture
def mint_pat(pats_client: PatsClient) -> Iterator[MintPat]:
    """Factory: mint one PAT and return the 201 ``token`` object; all are revoked on teardown.

    ``mint_pat(as_user=None, **body)``. The admin owns it unless ``as_user`` is the
    ``second_user``. ``name`` defaults to a unique one; ``scopes`` / ``expiryDays``
    pass through.
    """
    created: list[str] = []

    def _mint(as_user: SecondUser | None = None, **body: Any) -> dict[str, Any]:
        body.setdefault("name", pat_name())
        if as_user is None:
            resp = pats_client.create(body)
        else:
            resp = request_as(as_user, "POST", json=body)
        assert resp.status_code == 201, f"Minting a PAT failed: {resp.status_code} {resp.text[:300]}"
        token: dict[str, Any] = resp.json()["token"]
        created.append(token["id"])
        return token

    try:
        yield _mint
    finally:
        # The admin route reaches any owner's token; 404 means the test already revoked it.
        for token_id in created:
            pats_client.admin_revoke(token_id)


@pytest.fixture
def service_token(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """The raw ``phsvc_`` token of a throwaway service account; the account is deleted on teardown."""
    slug = f"spec-audit-pat-{uuid.uuid4().hex[:10]}"
    resp = pipeshub_client.request(
        "POST", "/api/v1/service-accounts", json={"slug": slug, "fullName": f"Spec audit {slug}"}
    )
    assert resp.status_code == 201, f"seeding a service account failed: {resp.status_code} {resp.text[:300]}"
    account_id = resp.json()["id"]
    try:
        resp = pipeshub_client.request(
            "POST",
            "/api/v1/service-tokens",
            json={"serviceAccountId": account_id, "name": "spec-audit pat probe", "scopes": ["kb:read"]},
        )
        assert resp.status_code == 201, f"minting a service token failed: {resp.status_code} {resp.text[:300]}"
        yield resp.json()["token"]["accessToken"]
    finally:
        # Deleting the account also revokes the token.
        pipeshub_client.request("DELETE", f"/api/v1/service-accounts/{account_id}")
