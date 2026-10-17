"""Fixtures for the credential-invalidation scenarios.

Each test gets its own account. The shared ``second_user`` fixture is
session-scoped, and these tests change a password and block an account — state
that would leak into whichever test ran next and make it pass or fail for
reasons that have nothing to do with what it checks. Paying for one account per
test is the cost of each test meaning what it says.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator

import pytest
import requests

from helper.second_user import SecondUser, create_second_user, delete_second_user

logger = logging.getLogger("security-fixtures")

# authenticateWithPassword in userAccount.controller.ts locks the account on the
# fifth wrong password, in the same request, before it answers.
WRONG_ATTEMPTS_TO_BLOCK = 5


def _try_password(base_url: str, email: str, password: str) -> requests.Response:
    session = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth", json={"email": email}, timeout=30
    )
    session_token = session.headers.get("x-session-token")
    assert session.status_code < 400 and session_token, (
        f"initAuth for {email} failed, so the password step was never reached: "
        f"{session.status_code} {session.text[:160]}"
    )
    return requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token, "Content-Type": "application/json"},
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=30,
    )


@pytest.fixture
def fresh_user(pipeshub_client) -> Iterator[SecondUser]:
    """A brand-new non-admin account, discarded after the test."""
    pipeshub_client._ensure_access_token()
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        # Raised, not logged. These accounts are real logins in a shared
        # environment, and one that silently outlives its test is a credential
        # nobody knows exists. A teardown error is reported alongside any test
        # failure rather than replacing it, so nothing is hidden either way.
        delete_second_user(pipeshub_client, user, strict=True)


@pytest.fixture
def block_account(fresh_user: SecondUser) -> Callable[[], None]:
    """Block the account the way a real attacker would trigger it.

    Driven through repeated failed logins rather than by writing the flag into
    the database, so the test exercises the product's own blocking path. A
    directly written flag would prove only that the test can write to MongoDB.
    """
    from helper.config import TEST_USER_PASSWORD

    def _block() -> None:
        for attempt in range(WRONG_ATTEMPTS_TO_BLOCK):
            _try_password(fresh_user.base_url, fresh_user.email, f"DefinitelyWrong{attempt}!")

        # A locked account is answered exactly like a wrong password, so the
        # wording can't show the lock. The right password being refused can: it
        # opened this account's session moments ago, in fresh_user.
        probe = _try_password(fresh_user.base_url, fresh_user.email, TEST_USER_PASSWORD)
        # The body is left out: when the lock failed it holds live tokens.
        token_returned = "accessToken" in probe.text
        assert probe.status_code == 400 and not token_returned, (
            f"{WRONG_ATTEMPTS_TO_BLOCK} wrong logins did not block "
            f"{fresh_user.email}: the right password afterwards got "
            f"{probe.status_code} (access token returned: {token_returned})"
        )
        logger.info("Blocked %s via failed logins", fresh_user.email)

    return _block
