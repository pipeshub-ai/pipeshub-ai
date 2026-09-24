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

# userAccount.controller.ts:228 and :1131. The counter is incremented before the
# comparison, so the block lands on the fifth wrong attempt, not after it.
WRONG_ATTEMPTS_TO_BLOCK = 5


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

    def _block() -> None:
        for attempt in range(WRONG_ATTEMPTS_TO_BLOCK):
            session = requests.post(
                f"{fresh_user.base_url}/api/v1/userAccount/initAuth",
                json={"email": fresh_user.email},
                timeout=30,
            )
            requests.post(
                f"{fresh_user.base_url}/api/v1/userAccount/authenticate",
                headers={
                    "x-session-token": session.headers.get("x-session-token", ""),
                    "Content-Type": "application/json",
                },
                json={
                    "method": "password",
                    "credentials": {"password": f"DefinitelyWrong{attempt}!"},
                    "email": fresh_user.email,
                },
                timeout=30,
            )

        # Confirm the account really is blocked, so a test that depends on it
        # cannot quietly measure an unblocked account instead.
        session = requests.post(
            f"{fresh_user.base_url}/api/v1/userAccount/initAuth",
            json={"email": fresh_user.email},
            timeout=30,
        )
        probe = requests.post(
            f"{fresh_user.base_url}/api/v1/userAccount/authenticate",
            headers={
                "x-session-token": session.headers.get("x-session-token", ""),
                "Content-Type": "application/json",
            },
            json={
                "method": "password",
                "credentials": {"password": "StillWrong!"},
                "email": fresh_user.email,
            },
            timeout=30,
        )
        assert "blocked" in probe.text.lower() or "disabled" in probe.text.lower(), (
            f"{WRONG_ATTEMPTS_TO_BLOCK} wrong logins did not block "
            f"{fresh_user.email}; the reply was {probe.status_code} {probe.text[:160]}"
        )
        logger.info("Blocked %s via failed logins", fresh_user.email)

    return _block
