"""Shared fixtures for the strict OpenAPI audit of /api/v1/orgAuthConfig."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
# The account, sign-in policy and stored-setting helpers live with the userAccount audit.
_USER_ACCOUNT_AUDIT = Path(__file__).resolve().parents[1] / "spec_audit_user_account"
for _p in (
    _INTEGRATION_ROOT,
    _INTEGRATION_ROOT / "response-validation" / "helper",
    _USER_ACCOUNT_AUDIT,
    Path(__file__).parent,
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper import local_auth  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402

from org_auth_config_audit_support import (  # noqa: E402
    OrgAuthConfigClient,
    mint_access_token,
)
from user_account_audit_support import (  # noqa: E402
    SignInPolicy,
    UserAccountAuditClient,
    bearer,
    create_account,
    delete_member,
    forget_mail,
    stored_signing_secret,
)

MintAccessToken = Callable[..., str]


@pytest.fixture(scope="session")
def org_auth_config_client(pipeshub_client: PipeshubClient) -> OrgAuthConfigClient:
    return OrgAuthConfigClient(pipeshub_client)


@pytest.fixture(scope="session")
def admin_session_token(pipeshub_client: PipeshubClient) -> str:
    """The shared admin signed in with a password: these routes accept only a session token."""
    return local_auth.obtain_user_session_token(pipeshub_client.base_url)


@pytest.fixture(scope="session")
def admin_headers(admin_session_token: str) -> dict[str, str]:
    return bearer(admin_session_token)


@pytest.fixture(scope="session")
def sign_in_policy(
    pipeshub_client: PipeshubClient, admin_headers: dict[str, str]
) -> SignInPolicy:
    return SignInPolicy(pipeshub_client, admin_headers)


@pytest.fixture(scope="module")
def member_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """A non-admin member's own session access token."""
    account = create_account(pipeshub_client, "spec-audit-org-auth")
    try:
        tokens = UserAccountAuditClient(pipeshub_client).sign_in(account)
        yield bearer(tokens.access)
    finally:
        delete_member(pipeshub_client, account)
        forget_mail(account.email)


@pytest.fixture(scope="session")
def access_token(
    org_auth_config_client: OrgAuthConfigClient, admin_session_token: str
) -> MintAccessToken:
    """Factory: ``access_token(**claims)`` -> a token signed with the deployment's access-token key.

    The key is read from the configuration store and proven by a token carrying only the
    admin's ``userId`` and ``orgId`` being let in.
    """
    try:
        secret = stored_signing_secret("jwtSecret")
    except AssertionError as exc:
        pytest.fail(f"the access-token signing key cannot be read: {exc}")
    admin = local_auth.jwt_claims(admin_session_token)
    probe = org_auth_config_client.auth_methods(
        headers=bearer(mint_access_token(secret, userId=admin["userId"], orgId=admin["orgId"]))
    )
    if probe.status_code != 200:
        pytest.fail(
            "the jwtSecret in the configuration store is not the key this Node process verifies "
            f"access tokens with (a token signed with it got {probe.status_code})"
        )

    def _mint(**claims: object) -> str:
        return mint_access_token(secret, **claims)

    return _mint


@pytest.fixture(scope="session")
def admin_claims(admin_session_token: str) -> dict[str, str]:
    claims = local_auth.jwt_claims(admin_session_token)
    return {"userId": claims["userId"], "orgId": claims["orgId"]}
