"""Shared fixtures for the strict OpenAPI audit of /api/v1/users."""

from __future__ import annotations

import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.users_client import UsersClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import (  # noqa: E402
    SecondUser,
    create_second_user,
    delete_second_user,
    second_user,  # noqa: F401 - fixture
)

from users_audit_support import (  # noqa: E402
    USER_LOOKUP_SCOPE,
    MintScopedToken,
    SeededUser,
    SeedUser,
    delete_credentials,
    delete_display_pictures,
    delete_invite_notifications,
    forget_access_token,
    forget_mail,
    insert_blocked_credentials,
    mint_narrow_scope_token,
    mint_scoped_token,
    request_with_token,
    scoped_jwt_secret,
    users_with_emails,
    wait_for_mail_to_settle,
)


@pytest.fixture(scope="session")
def scoped_token(pipeshub_client: PipeshubClient) -> MintScopedToken:
    """Factory: ``scoped_token(USER_LOOKUP_SCOPE)`` -> a service token the deployment accepts.

    Extra positional scopes and keyword claims are passed through; ``userId`` and
    ``orgId`` default to the shared admin's. Fails when SCOPED_JWT_SECRET is unset
    or is not the secret the deployment verifies with, since nothing can then get
    past a scoped route's signature check.
    """
    secret = scoped_jwt_secret()
    if not secret:
        pytest.fail(
            "SCOPED_JWT_SECRET is not set: this route accepts only scoped service tokens"
        )

    def _mint(*scopes: str, ttl_seconds: int = 3600, **claims: Any) -> str:
        claims.setdefault("userId", pipeshub_client.acting_user_id)
        claims.setdefault("orgId", pipeshub_client.org_id)
        return mint_scoped_token(secret, list(scopes), ttl_seconds=ttl_seconds, **claims)

    # A deployment that generated its own secret keeps it in the KV store, so the
    # env value can be set and still be the wrong key.
    probe = request_with_token(
        pipeshub_client,
        _mint(USER_LOOKUP_SCOPE),
        "GET",
        "/email/exists",
        json={"email": f"spec-audit-probe-{uuid.uuid4().hex[:10]}@test-pipeshub.com"},
    )
    if probe.status_code == 401:
        pytest.fail(
            "SCOPED_JWT_SECRET is not the secret this deployment verifies scoped "
            f"tokens with (a correctly scoped probe token got 401: {probe.text[:120]})"
        )

    return _mint


@pytest.fixture(scope="session")
def smtp_relay_reachable() -> None:
    """Fail when nothing accepts connections on the relay at SMTP_PORT.

    Saving an SMTP config does not check the relay, so a route that really sends
    mail answers 500 when the configured relay is down. SMTP_HOST is the name the
    API uses (``mailpit`` inside the CI compose network); the runner reaches the
    same relay on MAILPIT_URL's host.
    """
    mailpit_url = os.getenv("MAILPIT_URL", "").strip()
    host = urlsplit(mailpit_url).hostname if mailpit_url else os.getenv("SMTP_HOST", "").strip()
    port = os.getenv("SMTP_PORT", "").strip()
    if not host or not port.isdigit():
        pytest.fail("SMTP_HOST/SMTP_PORT not set: no relay to deliver the mail to")
    try:
        socket.create_connection((host, int(port)), timeout=5).close()
    except OSError as exc:
        pytest.fail(f"no SMTP relay is listening on {host}:{port} ({exc})")


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


@pytest.fixture
def blocked_user(
    seed_user: SeedUser, pipeshub_client: PipeshubClient
) -> Iterator[SeededUser]:
    """A seeded member whose credential row is locked out, as after too many wrong passwords."""
    user = seed_user()
    insert_blocked_credentials(pipeshub_client.org_id, str(user["_id"]))
    try:
        yield user
    finally:
        delete_credentials(str(user["_id"]))


@pytest.fixture(scope="module")
def picture_member(pipeshub_client: PipeshubClient) -> Iterator[SecondUser]:
    """A logged-in member of its own, since /users/dp always acts on the caller's picture."""
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        delete_display_pictures(user.user_id)
        delete_second_user(pipeshub_client, user, strict=True)


@pytest.fixture
def no_picture(picture_member: SecondUser) -> Iterator[SecondUser]:
    """``picture_member`` with no display-picture row at the start and end of the test."""
    delete_display_pictures(picture_member.user_id)
    try:
        yield picture_member
    finally:
        delete_display_pictures(picture_member.user_id)


@pytest.fixture
def narrow_scope_token(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """An OAuth access token of the suite's client that holds no ``user:*`` scope."""
    token = mint_narrow_scope_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds)
    try:
        yield token
    finally:
        forget_access_token(token)


@pytest.fixture
def mail_sink() -> Iterator[list[str]]:
    """Addresses a test sends mail to; everything Mailpit holds for them is deleted afterwards."""
    addresses: list[str] = []
    try:
        yield addresses
    finally:
        if addresses:
            wait_for_mail_to_settle(addresses)
        forget_mail(addresses)


@pytest.fixture
def invitee(users_client: UsersClient, mail_sink: list[str]) -> Iterator[Callable[[], str]]:
    """Factory of fresh addresses on a reserved domain; accounts an invite made for them are deleted.

    Mailpit is the relay, so the invitation mail is caught there and removed too.
    """
    issued: list[str] = []

    def _new() -> str:
        address = f"spec-audit-inv-{uuid.uuid4().hex[:10]}@example.com"
        issued.append(address)
        mail_sink.append(address)
        return address

    try:
        yield _new
    finally:
        leftovers: list[str] = []
        for user in users_with_emails(issued) if issued else []:
            if user.get("isDeleted"):
                continue
            resp = users_client.delete_user(str(user["_id"]))
            if resp.status_code != 200:
                leftovers.append(f"{user['email']}: {resp.status_code} {resp.text[:200]}")
        assert not leftovers, f"invited users were not removed: {leftovers}"


@pytest.fixture(scope="module")
def upload_member(pipeshub_client: PipeshubClient) -> Iterator[SecondUser]:
    """A member of its own for file imports, so the import's notification is not the admin's."""
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        delete_invite_notifications(user.user_id)
        delete_second_user(pipeshub_client, user, strict=True)
