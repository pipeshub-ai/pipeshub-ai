"""Shared fixtures for the strict OpenAPI audit of /api/v1/saml."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

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

from saml_audit_support import (  # noqa: E402
    IDP_ENTRY_POINT,
    IDP_PLATFORM,
    DummyIdp,
    SamlClient,
    authn_request_issuer,
    register_idp,
    saml_strategy_registered,
    scoped_jwt_secret,
)
from user_account_audit_support import (  # noqa: E402
    SSO_CONFIG_PATH,
    SSO_SETTING,
    DisposableMember,
    SignInPolicy,
    UserAccountAuditClient,
    bearer,
    create_member,
    delete_member,
    preserved_setting,
    steps,
)


@pytest.fixture(scope="session")
def saml_client(pipeshub_client: PipeshubClient) -> SamlClient:
    return SamlClient(pipeshub_client)


@pytest.fixture(scope="session")
def user_account_audit_client(pipeshub_client: PipeshubClient) -> UserAccountAuditClient:
    return UserAccountAuditClient(pipeshub_client)


@pytest.fixture(scope="session")
def saml_configured(saml_client: SamlClient) -> bool:
    """Whether an IdP was already registered with this Node process when the SAML tests began."""
    return saml_strategy_registered(saml_client)


@pytest.fixture(scope="session")
def scoped_secret() -> str:
    secret = scoped_jwt_secret()
    if not secret:
        pytest.fail(
            "SCOPED_JWT_SECRET is not set in the test environment: a fetch:config service "
            "token cannot be minted without the deployment's scoped JWT secret"
        )
    return secret


@pytest.fixture(scope="session")
def dummy_idp(
    pipeshub_client: PipeshubClient, saml_configured: bool
) -> Iterator[DummyIdp]:
    """A throwaway IdP saved as the org's SSO setting for the rest of the run.

    The stored setting is put back afterwards. The passport strategy it registered is
    not: Node has no way to drop one, so it stays until the process restarts.
    ``saml_configured`` is read first so it reports the state before this fixture.
    """
    stored = pipeshub_client.request("GET", SSO_CONFIG_PATH)
    assert stored.status_code == 200, stored.text[:500]
    setting = stored.json()
    if setting.get("entryPoint") and setting.get("samlPlatform") != IDP_PLATFORM:
        pytest.fail(
            "this deployment has a real SSO identity provider saved "
            f"({setting.get('samlPlatform') or setting['entryPoint']}); the audit will not replace it"
        )
    idp = DummyIdp()
    with preserved_setting(SSO_SETTING):
        register_idp(pipeshub_client, idp)
        yield idp


@pytest.fixture(scope="session")
def service_provider(saml_client: SamlClient, dummy_idp: DummyIdp) -> str:
    """This deployment's SAML entity id, the audience an assertion must name."""
    resp = saml_client.sign_in()
    assert resp.status_code == 302, resp.text[:500]
    location = resp.headers["Location"]
    assert location.startswith(IDP_ENTRY_POINT), location[:200]
    return authn_request_issuer(location)


@pytest.fixture(scope="session")
def sign_in_policy(pipeshub_client: PipeshubClient) -> SignInPolicy:
    """orgAuthConfig accepts only the session token of an admin signed in with a password."""
    token = local_auth.obtain_user_session_token(pipeshub_client.base_url)
    return SignInPolicy(pipeshub_client, bearer(token))


@pytest.fixture(scope="module")
def saml_sign_in_allowed(sign_in_policy: SignInPolicy) -> Iterator[None]:
    """SAML next to the password in the org's one sign-in step, for one file."""
    with sign_in_policy.temporarily(steps(["password", "samlSso"])):
        yield


@pytest.fixture(scope="module")
def saml_member(pipeshub_client: PipeshubClient) -> Iterator[DisposableMember]:
    """The account the dummy IdP signs in."""
    member = create_member(pipeshub_client, "spec-audit-saml")
    try:
        yield member
    finally:
        delete_member(pipeshub_client, member)
