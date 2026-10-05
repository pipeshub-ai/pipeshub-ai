"""Shared fixtures for the strict OpenAPI audit of /api/v1/userAccount."""

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

from helper.clients.users_client import UsersClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

from user_account_audit_support import (  # noqa: E402
    VALIDATE_EMAIL_SCOPE,
    DisposableMember,
    MintEmailChangeToken,
    UserAccountAuditClient,
    mint_scoped_token,
    scoped_jwt_secret,
    unused_email,
    user_action_key,
)


@pytest.fixture(scope="session")
def user_account_audit_client(pipeshub_client: PipeshubClient) -> UserAccountAuditClient:
    return UserAccountAuditClient(pipeshub_client)


@pytest.fixture(scope="session")
def email_change_token(pipeshub_client: PipeshubClient) -> MintEmailChangeToken:
    """Factory: ``email_change_token(user_id, new_email)`` -> a token validateEmailChange accepts.

    Signed like the link in the verification mail. ``scopes=[...]``, ``ttl_seconds=``
    and extra claims override the defaults; ``orgId`` defaults to the shared org.
    Skips when SCOPED_JWT_SECRET is unset, since the token cannot be forged then.
    """
    secret = scoped_jwt_secret()
    if not secret:
        pytest.skip(
            "SCOPED_JWT_SECRET is not set: validateEmailChange accepts only an email:validate token"
        )
    key = user_action_key(secret)

    def _mint(
        user_id: str,
        new_email: str,
        *,
        scopes: list[str] | None = None,
        ttl_seconds: int = 1200,
        **claims: Any,
    ) -> str:
        claims.setdefault("orgId", pipeshub_client.org_id)
        return mint_scoped_token(
            key,
            [VALIDATE_EMAIL_SCOPE] if scopes is None else scopes,
            ttl_seconds=ttl_seconds,
            userId=user_id,
            newEmail=new_email,
            **claims,
        )

    return _mint


@pytest.fixture
def disposable_member(
    users_client: UsersClient, pipeshub_client: PipeshubClient
) -> Iterator[DisposableMember]:
    """A member whose email a test may change; the shared admin's must never be."""
    email = unused_email("spec-audit-member")
    resp = users_client.create_user(email, f"Spec Audit {uuid.uuid4().hex[:8]}")
    assert resp.status_code in (200, 201), f"createUser failed: {resp.status_code} {resp.text[:300]}"
    user = resp.json()
    user_id = str(user.get("_id") or user.get("id") or "")
    assert user_id, f"createUser response has no id: {sorted(user)}"
    try:
        yield DisposableMember(
            user_id=user_id,
            org_id=str(user.get("orgId") or pipeshub_client.org_id),
            email=email,
        )
    finally:
        users_client.delete_user(user_id)
