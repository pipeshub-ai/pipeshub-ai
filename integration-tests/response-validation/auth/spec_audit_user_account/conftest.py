"""Shared fixtures for the strict OpenAPI audit of /api/v1/userAccount."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper import local_auth, mailpit  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

from user_account_audit_support import (  # noqa: E402
    MISSING_USER_ID,
    OAUTH_SETTING,
    OTHER_SCOPE,
    VALIDATE_EMAIL_SCOPE,
    Account,
    DisposableMember,
    MintEmailChangeToken,
    MintUserToken,
    OAuthProviderStub,
    SignInPolicy,
    Tokens,
    UserAccountAuditClient,
    bearer,
    configure_oauth,
    create_account,
    create_member,
    delete_member,
    forget_email_activity,
    forget_mail,
    mint_scoped_token,
    preserved_setting,
    scoped_jwt_secret,
    unused_email,
    user_action_key,
)


@pytest.fixture(scope="session")
def user_account_audit_client(pipeshub_client: PipeshubClient) -> UserAccountAuditClient:
    return UserAccountAuditClient(pipeshub_client)


@pytest.fixture(scope="session")
def user_token(
    pipeshub_client: PipeshubClient, user_account_audit_client: UserAccountAuditClient
) -> MintUserToken:
    """Factory: ``user_token(scope, user_id, **claims)`` -> a token signed like the ones Node hands users.

    ``scopes=[...]`` and ``ttl_seconds=`` override the defaults; ``orgId`` defaults to the shared org.
    """
    secret = scoped_jwt_secret()
    if not secret:
        pytest.fail(
            "SCOPED_JWT_SECRET is not set in the test environment: reset-link, refresh and "
            "email-change tokens cannot be minted without the deployment's scoped JWT secret"
        )
    key = user_action_key(secret)

    # A wrong-scope token is refused either way, but a good signature gets
    # "Invalid scope" and a bad one "Invalid token" (AuthTokenService.verifyScopedToken).
    probe = user_account_audit_client.validate_email_change(
        token=mint_scoped_token(
            key,
            [OTHER_SCOPE],
            userId=MISSING_USER_ID,
            newEmail=unused_email(),
            orgId=pipeshub_client.org_id,
        )
    )
    assert probe.status_code == 401, probe.text[:500]
    if probe.json()["error"]["message"] != "Invalid scope":
        pytest.fail(
            "SCOPED_JWT_SECRET is not the scoped JWT secret this deployment verifies with "
            "(it answers 'Invalid token' to a token signed with it), so user tokens cannot be minted"
        )

    def _mint(
        scope: str,
        user_id: str,
        *,
        scopes: list[str] | None = None,
        ttl_seconds: int = 1200,
        **claims: Any,
    ) -> str:
        claims.setdefault("orgId", pipeshub_client.org_id)
        return mint_scoped_token(
            key,
            [scope] if scopes is None else scopes,
            ttl_seconds=ttl_seconds,
            userId=user_id,
            **claims,
        )

    return _mint


@pytest.fixture(scope="session")
def email_change_token(user_token: MintUserToken) -> MintEmailChangeToken:
    """Factory: ``email_change_token(user_id, new_email)`` -> a token validateEmailChange accepts."""

    def _mint(user_id: str, new_email: str, **overrides: Any) -> str:
        return user_token(VALIDATE_EMAIL_SCOPE, user_id, newEmail=new_email, **overrides)

    return _mint


@pytest.fixture
def disposable_member(pipeshub_client: PipeshubClient) -> Iterator[DisposableMember]:
    """A member whose email a test may change; the shared admin's must never be."""
    member = create_member(pipeshub_client)
    try:
        yield member
    finally:
        delete_member(pipeshub_client, member)


@pytest.fixture
def account(pipeshub_client: PipeshubClient) -> Iterator[Account]:
    """A member with a password, for tests that change or end its sessions."""
    created = create_account(pipeshub_client)
    try:
        yield created
    finally:
        delete_member(pipeshub_client, created)
        forget_mail(created.email)


@pytest.fixture(scope="module")
def module_account(pipeshub_client: PipeshubClient) -> Iterator[Account]:
    """One member with a password for a whole file; only for tests that leave it able to sign in."""
    created = create_account(pipeshub_client)
    try:
        yield created
    finally:
        delete_member(pipeshub_client, created)
        forget_mail(created.email)


@pytest.fixture(scope="module")
def module_tokens(
    user_account_audit_client: UserAccountAuditClient, module_account: Account
) -> Tokens:
    return user_account_audit_client.sign_in(module_account)


@pytest.fixture
def stray_email() -> Iterator[str]:
    """An address with no account; the activity rows written for it are removed afterwards."""
    email = unused_email("spec-audit-nobody")
    try:
        yield email
    finally:
        forget_email_activity(email)
        forget_mail(email)


@pytest.fixture(scope="session")
def admin_session_headers(pipeshub_client: PipeshubClient) -> dict[str, str]:
    """The shared admin signed in with a password: orgAuthConfig accepts only a session token."""
    return bearer(local_auth.obtain_user_session_token(pipeshub_client.base_url))


@pytest.fixture(scope="session")
def sign_in_policy(
    pipeshub_client: PipeshubClient, admin_session_headers: dict[str, str]
) -> SignInPolicy:
    return SignInPolicy(pipeshub_client, admin_session_headers)


@pytest.fixture(scope="session")
def mailbox() -> None:
    """Mailpit must answer, or nothing about delivered mail can be asserted."""
    try:
        mailpit.message_ids("nobody@example.com")
    except mailpit.MailpitUnavailable as exc:
        pytest.fail(f"{exc}. Set MAILPIT_URL to Mailpit's web API.")


@pytest.fixture(scope="module")
def oauth_provider(pipeshub_client: PipeshubClient) -> Iterator[OAuthProviderStub]:
    """A local OAuth provider saved as the org's generic OAuth sign-in, with JIT off.

    The stored setting is put back when the file is done.
    """
    stub = OAuthProviderStub()
    try:
        with preserved_setting(OAUTH_SETTING):
            configure_oauth(pipeshub_client, stub.config())
            yield stub
    finally:
        stub.close()
