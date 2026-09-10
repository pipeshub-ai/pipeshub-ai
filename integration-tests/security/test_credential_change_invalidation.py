"""Changing a credential should end the sessions that credential opened.

Two events are expected to end an existing session: changing the account's
password, and blocking the account. The first works. The second does not.

The mechanism is in ``auth.middleware.ts``. On every request it loads the most
recent ``UserActivities`` row whose type is in ``SESSION_INVALIDATING_ACTIVITIES``
— currently LOGOUT, PASSWORD_CHANGED and ROLE_CHANGED — and rejects the token if
that activity is newer than the token's ``iat``. It also rejects a token whose
user is flagged ``isDeleted``.

Blocking writes no such activity, and ``isBlocked`` is not among the fields the
middleware checks. So a blocked account's existing sessions keep working until
the token expires, which is 24 hours.
"""

from __future__ import annotations

import logging
import time

import pytest
import requests

logger = logging.getLogger("security-invalidation")

pytestmark = [pytest.mark.integration, pytest.mark.security]

NEW_PASSWORD = "RotatedPass789!"

# auth.middleware.ts compares the activity timestamp against iat + 1000ms, and a
# JWT's iat has one-second resolution. Tests therefore put a few seconds between
# issuing a token and the event that should invalidate it, so they measure the
# rule rather than that window.
GRACE_MARGIN_SECONDS = 3

BLOCK_ISSUE = (
    "Blocking an account does not end its existing sessions. auth.middleware.ts "
    "rejects a token whose user is isDeleted, and rejects one older than the "
    "latest SESSION_INVALIDATING_ACTIVITIES row (LOGOUT, PASSWORD_CHANGED, "
    "ROLE_CHANGED). Blocking writes no such row and isBlocked is never read, so "
    "the session survives for the token's remaining life — up to 24 hours."
)

ANY_USER_ROUTE = "/api/v1/knowledgeBase"


def _authorised(base_url: str, token: str, path: str = ANY_USER_ROUTE) -> int:
    return requests.get(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ).status_code


def _change_password(base_url: str, token: str, current: str, new: str) -> int:
    return requests.post(
        f"{base_url}/api/v1/userAccount/password/reset",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"currentPassword": current, "newPassword": new},
        timeout=30,
    ).status_code


def _attempt_login(base_url: str, email: str, password: str) -> int:
    session = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth", json={"email": email}, timeout=30
    )
    return requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={
            "x-session-token": session.headers.get("x-session-token", ""),
            "Content-Type": "application/json",
        },
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=30,
    ).status_code


class TestPasswordChange:
    """This works today, and is worth a guard so it keeps working."""

    def test_a_token_issued_before_the_change_is_rejected(self, fresh_user) -> None:
        """The reason to change a password is that someone else may have it.

        Rotating it while sessions opened with the old one stay live would
        defeat the point. They do not — this is the behaviour that guards it.
        """
        from helper.config import TEST_USER_PASSWORD

        old_token = fresh_user.token
        assert _authorised(fresh_user.base_url, old_token) == 200, (
            "The token did not work before the change, so this proves nothing."
        )
        time.sleep(GRACE_MARGIN_SECONDS)

        assert (
            _change_password(
                fresh_user.base_url, old_token, TEST_USER_PASSWORD, NEW_PASSWORD
            )
            == 200
        )

        status = _authorised(fresh_user.base_url, old_token)
        assert status == 401, (
            f"A token issued before the password change still works "
            f"(HTTP {status}). Anyone holding a session opened with the old "
            "password keeps their access until the token expires."
        )

    def test_the_old_password_stops_working(self, fresh_user) -> None:
        from helper.config import TEST_USER_PASSWORD

        _change_password(
            fresh_user.base_url, fresh_user.token, TEST_USER_PASSWORD, NEW_PASSWORD
        )
        status = _attempt_login(
            fresh_user.base_url, fresh_user.email, TEST_USER_PASSWORD
        )
        assert status >= 400, (
            f"Logging in with the old password returned {status}. A changed "
            "password that still opens a session has not been changed."
        )


class TestBlockingAnAccount:
    """The test list's 'User Blocked → JWT token shouldn't work'."""

    @pytest.mark.xfail(strict=True, reason=BLOCK_ISSUE)
    def test_a_token_issued_before_the_block_is_rejected(
        self, fresh_user, block_account
    ) -> None:
        """Blocking is the control reached for when an account is compromised.

        If the session it was compromised through survives, the control has not
        done the one thing it exists to do. Blocking a password does stop new
        logins — this is about the session already open.
        """
        token = fresh_user.token
        assert _authorised(fresh_user.base_url, token) == 200, (
            "The token did not work before the block, so this proves nothing."
        )
        time.sleep(GRACE_MARGIN_SECONDS)

        block_account()
        time.sleep(GRACE_MARGIN_SECONDS)

        status = _authorised(fresh_user.base_url, token)
        assert status == 401, (
            f"A token issued before the account was blocked still works "
            f"(HTTP {status}). Blocking stops new logins and leaves every "
            "session already open running until its token expires."
        )
