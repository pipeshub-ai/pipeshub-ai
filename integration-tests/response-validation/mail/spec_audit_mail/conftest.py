"""Shared fixtures for the strict OpenAPI audit of /api/v1/mail."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from mail_audit_support import (  # noqa: E402
    OTHER_SCOPE,
    MailClient,
    MintScopedToken,
    mint_scoped_token,
    scoped_jwt_secret,
)


@pytest.fixture(scope="session")
def mail_client(pipeshub_client: PipeshubClient) -> MailClient:
    return MailClient(pipeshub_client)


@pytest.fixture(scope="session")
def scoped_token(
    pipeshub_client: PipeshubClient, mail_client: MailClient
) -> MintScopedToken:
    """Factory: ``scoped_token(SEND_MAIL_SCOPE)`` -> a service token the deployment accepts.

    Extra positional scopes and keyword claims are passed through; ``userId`` and
    ``orgId`` default to the shared admin's. Skips when SCOPED_JWT_SECRET is unset
    or is not the secret this deployment verifies scoped tokens with.
    """
    secret = scoped_jwt_secret()
    if not secret:
        pytest.skip(
            "SCOPED_JWT_SECRET is not set: mail routes accept only scoped service tokens"
        )

    def _mint(*scopes: str, ttl_seconds: int = 3600, **claims: Any) -> str:
        claims.setdefault("userId", pipeshub_client.acting_user_id)
        claims.setdefault("orgId", pipeshub_client.org_id)
        return mint_scoped_token(secret, list(scopes), ttl_seconds=ttl_seconds, **claims)

    # A wrong scope is refused either way, but a good signature gets "Invalid scope"
    # and a bad one "Invalid token" (AuthTokenService.verifyScopedToken).
    probe = mail_client.update_smtp_config(token=_mint(OTHER_SCOPE))
    assert probe.status_code == 401, probe.text[:500]
    if probe.json()["error"]["message"] != "Invalid scope":
        pytest.skip(
            "SCOPED_JWT_SECRET is not the scoped JWT secret this deployment verifies "
            "with (it answers 'Invalid token' to a token signed with it), so a "
            "scoped service token cannot be minted"
        )

    return _mint
