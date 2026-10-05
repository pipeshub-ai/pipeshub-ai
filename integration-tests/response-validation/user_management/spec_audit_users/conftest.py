"""Shared fixtures for the strict OpenAPI audit of /api/v1/users."""

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
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from users_audit_support import (  # noqa: E402
    MintScopedToken,
    SeededUser,
    SeedUser,
    mint_scoped_token,
    scoped_jwt_secret,
)


@pytest.fixture(scope="session")
def scoped_token(pipeshub_client: PipeshubClient) -> MintScopedToken:
    """Factory: ``scoped_token(USER_LOOKUP_SCOPE)`` -> a service token the deployment accepts.

    Extra positional scopes and keyword claims are passed through; ``userId`` and
    ``orgId`` default to the shared admin's. Skips when SCOPED_JWT_SECRET is unset,
    since nothing can then get past a scoped route's token check.
    """
    secret = scoped_jwt_secret()
    if not secret:
        pytest.skip(
            "SCOPED_JWT_SECRET is not set: this route accepts only scoped service tokens"
        )

    def _mint(*scopes: str, ttl_seconds: int = 3600, **claims: Any) -> str:
        claims.setdefault("userId", pipeshub_client.acting_user_id)
        claims.setdefault("orgId", pipeshub_client.org_id)
        return mint_scoped_token(secret, list(scopes), ttl_seconds=ttl_seconds, **claims)

    return _mint


@pytest.fixture
def seed_user(users_client: UsersClient) -> Iterator[SeedUser]:
    """Factory: create one member through POST /users and return its document.

    The account has no credentials and has never logged in (``hasLoggedIn`` is
    false), and creating it sends no mail. ``seed_user(**fields)`` overrides the
    body; every account is deleted on teardown.
    """
    created: list[str] = []

    def _seed(**fields: Any) -> SeededUser:
        tag = uuid.uuid4().hex[:10]
        fields.setdefault("email", f"spec-audit-{tag}@test-pipeshub.com")
        fields.setdefault("full_name", f"Spec Audit User {tag}")
        resp = users_client.create_user(**fields)
        assert resp.status_code in (200, 201), (
            f"could not seed a user: {resp.status_code} {resp.text[:300]}"
        )
        user: SeededUser = resp.json()
        created.append(str(user["_id"]))
        return user

    try:
        yield _seed
    finally:
        leftovers: list[str] = []
        for user_id in created:
            resp = users_client.delete_user(user_id)
            if resp.status_code >= 400 and resp.status_code != 404:
                leftovers.append(f"{user_id}: {resp.status_code} {resp.text[:200]}")
        assert not leftovers, f"seeded users were not removed: {leftovers}"
