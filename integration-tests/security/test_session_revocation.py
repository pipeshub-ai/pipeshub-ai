"""Sessions that should have ended, checked at both doors a token can reach.

Each class opens two sessions for a throwaway account, as a laptop and a phone
would, then does the one thing that should end them: change the password, lock
the account, or delete it. Every token from before that moment must then be
refused, the refresh token as well as the access token, since a live refresh
token can mint a fresh access token at will.

The public entry point is Node (port 3000), which checks the account's recent
session-ending events on every request. The Python services behind it ask Node
whether a session is still live (``resolve_request_role`` in
``app/api/middlewares/auth.py``) and reuse the answer for
``SESSION_CHECK_CACHE_SECONDS``, which the integration stacks set to 2.

Lockout is the only way an account becomes blocked: there is no admin action
that blocks a user, only one that unblocks. The tests lock an account through
wrong passwords and read the lock from the account's record, so they hold
whatever the threshold; the threshold itself is checked in
``response-validation/auth/integration_test_emailed_sign_in.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlparse

import pytest
import requests
from jose import jwt
from pymongo import MongoClient

from helper import local_auth
from helper.clients.auth_client import UserAccountClient
from helper.config import MONGO_DB_NAME, MONGO_URI, TEST_USER_PASSWORD
from helper.pipeshub_client import PipeshubClient
from helper.second_user import (
    SecondUser,
    _delete_credentials,
    create_second_user,
    delete_second_user,
)
from helper.source_credentials import secrets_required

logger = logging.getLogger("security-session-revocation")

pytestmark = [pytest.mark.integration, pytest.mark.security]

NEW_PASSWORD = "RotatedPass789!"

# The session check compares an event against the token's iat plus one second,
# and iat has one-second resolution, so events are kept clear of that window.
GRACE_MARGIN_SECONDS = 3

# Far above any lockout threshold under discussion. The loop stops as soon as
# the account is locked, so this bounds a broken lockout, not the test's pace.
MAX_WRONG_PASSWORDS = 25

NODE_ROUTE = "/api/v1/knowledgeBase"
# Answers from the token alone, with no lookup of the user, so its status
# reflects the auth check and nothing else.
PYTHON_ROUTE = "/api/v1/connectors/registry"

# Signing context for refresh and reset tokens (backend/nodejs/apps/src/libs/utils/jwtKeys.ts).
_USER_ACTION_KEY_CONTEXT = b"pipeshub/jwt/user-action/v1"
_REFRESH_SCOPE = "token:refresh"

@dataclass(frozen=True)
class Session:
    access: str
    refresh: str


@dataclass
class TwoSessions:
    """An account signed in twice, with each session's standing before the event."""

    user: SecondUser
    first: Session
    second: Session
    # Status each access token got from the Python service before the event;
    # None when that service is not reachable from where the tests run.
    python_before: dict[str, int | None]

    def session(self, which: str) -> Session:
        return getattr(self, which)


# The session that performs the event (where one does), then an independent one.
SESSIONS = pytest.mark.parametrize(
    "which", ["first", "second"], ids=["first-session", "second-session"]
)


def _python_service_url(base_url: str) -> str:
    explicit = os.getenv("PIPESHUB_CONNECTOR_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.hostname}:8088"


def _node_status(user: SecondUser, token: str) -> int:
    return requests.get(
        f"{user.base_url}{NODE_ROUTE}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=user.timeout,
    ).status_code


def _python_status(user: SecondUser, token: str) -> int | None:
    try:
        return requests.get(
            f"{_python_service_url(user.base_url)}{PYTHON_ROUTE}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=user.timeout,
        ).status_code
    except requests.ConnectionError:
        return None


# A refusal is the status that means "sign in again", with no new token. Other
# 4xx and 5xx answers are also no token, but they are not the product refusing.
REFRESH_REFUSED_STATUS = 401


def _new_user(client: PipeshubClient) -> SecondUser:
    client._ensure_access_token()
    return create_second_user(client)


def _open_two_sessions(user: SecondUser, account: UserAccountClient) -> TwoSessions:
    """Sign in twice and confirm both sessions work before anything ends them.

    Without this, a refusal afterwards could mean the token never worked.
    """
    sessions = {}
    for which in ("first", "second"):
        access, refresh = local_auth.log_in_with_refresh_token(
            user.base_url, user.email, TEST_USER_PASSWORD, user.timeout
        )
        if not refresh:
            pytest.fail("Signing in returned no refresh token, so none can be tested.")
        if _node_status(user, access) != 200:
            pytest.fail(f"The {which} session's access token did not work to begin with.")
        if account.refresh_token(refresh).status_code != 200:
            pytest.fail(f"The {which} session's refresh token did not work to begin with.")
        sessions[which] = Session(access, refresh)
    return TwoSessions(
        user=user,
        first=sessions["first"],
        second=sessions["second"],
        python_before={
            which: _python_status(user, s.access) for which, s in sessions.items()
        },
    )


def _lock_by_wrong_passwords(
    user: SecondUser, org_id: str, account: UserAccountClient
) -> int:
    """Enter wrong passwords until the account is locked; return how many it took.

    The lock is read from the account's credential record, not inferred from a
    count, so this holds whatever the threshold turns out to be.
    """
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        credentials = mongo[MONGO_DB_NAME].userCredentials
        for attempt in range(1, MAX_WRONG_PASSWORDS + 1):
            session_token = account.init_auth(user.email).headers.get("x-session-token")
            if not session_token:
                pytest.fail("initAuth returned no session token, so no password was tried.")
            account.authenticate(session_token, user.email, f"DefinitelyWrong{attempt}!")
            row = credentials.find_one(
                {"userId": user.user_id, "orgId": org_id, "isDeleted": False},
                {"isBlocked": 1},
            )
            if row and row.get("isBlocked"):
                logger.info("Locked %s after %d wrong passwords", user.email, attempt)
                return attempt
    finally:
        mongo.close()
    pytest.fail(
        f"{MAX_WRONG_PASSWORDS} wrong passwords did not lock {user.email}, so "
        "nothing about a locked account can be checked."
    )


# How authenticate answers a wrong password, a locked account and an unknown
# email alike (WRONG_EMAIL_OR_PASSWORD).
WRONG_PASSWORD_STATUS = 400


def _password_login_status(account: UserAccountClient, email: str, password: str) -> tuple[int, bool]:
    started = account.init_auth(email)
    session_token = started.headers.get("x-session-token", "")
    if started.status_code != 200 or not session_token:
        pytest.fail(
            f"initAuth for {email} answered HTTP {started.status_code} with "
            f"{'a' if session_token else 'no'} session token, so no password was tried."
        )
    response = account.authenticate(session_token, email, password)
    return response.status_code, "accessToken" in response.text


def _assert_access_refused_at_node(sessions: TwoSessions, which: str, event: str) -> None:
    status = _node_status(sessions.user, sessions.session(which).access)
    assert status == 401, (
        f"An access token from the {which} session, issued before {event}, still "
        f"works at the public API (HTTP {status}). Whoever holds it keeps access "
        "until it expires."
    )


def _assert_refresh_refused(
    sessions: TwoSessions,
    which: str,
    event: str,
    account: UserAccountClient,
) -> None:
    """The refresh must be refused with 401 and no new token."""
    response = account.refresh_token(sessions.session(which).refresh)
    status = response.status_code
    # The body is left out of every message: when it holds a token, it is live.
    if "accessToken" in response.text:
        pytest.fail(
            f"A refresh token from the {which} session, issued before {event}, "
            f"minted a new session (HTTP {status}). It can keep minting access "
            "tokens for as long as it lives."
        )
    if status == REFRESH_REFUSED_STATUS:
        return
    message = (
        f"A refresh token from the {which} session, issued before {event}, got "
        f"HTTP {status}, not {REFRESH_REFUSED_STATUS}; no new token was returned."
    )
    pytest.fail(message)


def _assert_access_refused_at_python(sessions: TwoSessions, which: str, event: str) -> None:
    before = sessions.python_before[which]
    if before is None:
        pytest.skip(
            f"The connector service is not reachable at "
            f"{_python_service_url(sessions.user.base_url)}; set PIPESHUB_CONNECTOR_URL."
        )
    if before != 200:
        pytest.fail(
            f"The {which} session's token got HTTP {before} from the Python service "
            "before the event, so a refusal afterwards would prove nothing."
        )
    status = _python_status(sessions.user, sessions.session(which).access)
    assert status == 401, (
        f"An access token from the {which} session, issued before {event}, got "
        f"HTTP {status} from the Python service instead of being refused (401)."
    )


@pytest.fixture(scope="class")
def after_password_change(
    pipeshub_client: PipeshubClient, user_account_client: UserAccountClient
) -> Iterator[TwoSessions]:
    user = _new_user(pipeshub_client)
    try:
        sessions = _open_two_sessions(user, user_account_client)
        time.sleep(GRACE_MARGIN_SECONDS)
        changed = user_account_client.reset_password(
            sessions.first.access, TEST_USER_PASSWORD, NEW_PASSWORD
        )
        if changed.status_code != 200:
            pytest.fail(f"Changing the password failed with HTTP {changed.status_code}.")
        yield sessions
    finally:
        delete_second_user(pipeshub_client, user, strict=True)


@pytest.fixture(scope="class")
def after_lockout(
    pipeshub_client: PipeshubClient, user_account_client: UserAccountClient
) -> Iterator[TwoSessions]:
    user = _new_user(pipeshub_client)
    try:
        sessions = _open_two_sessions(user, user_account_client)
        time.sleep(GRACE_MARGIN_SECONDS)
        _lock_by_wrong_passwords(user, pipeshub_client.org_id, user_account_client)
        yield sessions
    finally:
        delete_second_user(pipeshub_client, user, strict=True)


@pytest.fixture(scope="class")
def after_deletion(
    pipeshub_client: PipeshubClient, user_account_client: UserAccountClient
) -> Iterator[TwoSessions]:
    user = _new_user(pipeshub_client)
    deleted = False
    try:
        sessions = _open_two_sessions(user, user_account_client)
        time.sleep(GRACE_MARGIN_SECONDS)
        response = pipeshub_client.request("DELETE", f"/api/v1/users/{user.user_id}")
        if response.status_code >= 400:
            pytest.fail(f"The admin could not delete the user: HTTP {response.status_code}.")
        deleted = True
        yield sessions
    finally:
        if deleted:
            # The deletion clears only the password hash; the seeded row goes too.
            _delete_credentials(pipeshub_client.org_id, user.user_id)
        else:
            delete_second_user(pipeshub_client, user, strict=True)


class TestPasswordChangeEndsEverySession:
    """Test list: password change — old password, old JWTs and old sessions stop working."""

    def test_the_old_password_is_refused_and_the_new_one_works(
        self, after_password_change: TwoSessions, user_account_client: UserAccountClient
    ) -> None:
        email = after_password_change.user.email
        status, token_returned = _password_login_status(
            user_account_client, email, TEST_USER_PASSWORD
        )
        assert status == WRONG_PASSWORD_STATUS and not token_returned, (
            f"Signing in with the old password returned HTTP {status}. A changed "
            "password that still opens a session has not been changed."
        )
        status, token_returned = _password_login_status(
            user_account_client, email, NEW_PASSWORD
        )
        assert status == 200 and token_returned, (
            f"The new password was refused (HTTP {status}), so the change never "
            "took effect and the refusal above proves nothing."
        )

    @SESSIONS
    def test_access_tokens_from_before_the_change_are_refused(
        self, after_password_change: TwoSessions, which: str
    ) -> None:
        _assert_access_refused_at_node(after_password_change, which, "the password change")

    @SESSIONS
    def test_refresh_tokens_from_before_the_change_are_refused(
        self,
        after_password_change: TwoSessions,
        which: str,
        user_account_client: UserAccountClient,
    ) -> None:
        _assert_refresh_refused(
            after_password_change, which, "the password change", user_account_client
        )

    @SESSIONS
    def test_the_python_services_refuse_access_tokens_from_before_the_change(
        self, after_password_change: TwoSessions, which: str
    ) -> None:
        _assert_access_refused_at_python(after_password_change, which, "the password change")


class TestLockedAccountEndsEverySession:
    """Test list: blocked user — their JWTs stop working and existing sessions end."""

    @SESSIONS
    def test_access_tokens_from_before_the_lock_are_refused(
        self, after_lockout: TwoSessions, which: str
    ) -> None:
        _assert_access_refused_at_node(after_lockout, which, "the account was locked")

    @SESSIONS
    def test_refresh_tokens_from_before_the_lock_are_refused(
        self, after_lockout: TwoSessions, which: str, user_account_client: UserAccountClient
    ) -> None:
        _assert_refresh_refused(
            after_lockout, which, "the account was locked", user_account_client
        )

    @SESSIONS
    def test_the_python_services_refuse_access_tokens_from_before_the_lock(
        self, after_lockout: TwoSessions, which: str
    ) -> None:
        _assert_access_refused_at_python(after_lockout, which, "the account was locked")


class TestAdminUnblock:
    """Test list: blocked user — after an admin unblocks them, a fresh login works."""

    def test_a_fresh_login_works_after_the_admin_unblocks(
        self,
        fresh_user: SecondUser,
        pipeshub_client: PipeshubClient,
        user_account_client: UserAccountClient,
    ) -> None:
        _lock_by_wrong_passwords(fresh_user, pipeshub_client.org_id, user_account_client)
        status, token_returned = _password_login_status(
            user_account_client, fresh_user.email, TEST_USER_PASSWORD
        )
        assert status == WRONG_PASSWORD_STATUS and not token_returned, (
            f"The right password opened a session on a locked account (HTTP {status}), "
            "so unblocking it would change nothing this test can see."
        )

        unblocked = pipeshub_client.request(
            "PUT", f"/api/v1/users/{fresh_user.user_id}/unblock"
        )
        assert unblocked.status_code == 200, (
            f"The admin could not unblock the account: HTTP {unblocked.status_code} "
            f"{unblocked.text[:200]}"
        )

        token = local_auth.log_in(
            fresh_user.base_url, fresh_user.email, TEST_USER_PASSWORD, fresh_user.timeout
        )
        status = _node_status(fresh_user, token)
        assert status == 200, (
            f"A session opened after the unblock does not work (HTTP {status})."
        )


class TestDeletedUserEndsEverySession:
    """Test list: deleted user — their JWTs stop working."""

    @SESSIONS
    def test_access_tokens_from_before_the_deletion_are_refused(
        self, after_deletion: TwoSessions, which: str
    ) -> None:
        _assert_access_refused_at_node(after_deletion, which, "the user was deleted")

    @SESSIONS
    def test_refresh_tokens_from_before_the_deletion_are_refused(
        self, after_deletion: TwoSessions, which: str, user_account_client: UserAccountClient
    ) -> None:
        _assert_refresh_refused(
            after_deletion,
            which,
            "the user was deleted",
            user_account_client,
        )

    @SESSIONS
    def test_the_python_services_refuse_access_tokens_from_before_the_deletion(
        self, after_deletion: TwoSessions, which: str
    ) -> None:
        _assert_access_refused_at_python(after_deletion, which, "the user was deleted")


def _scoped_jwt_secret() -> str:
    secret = os.getenv("SCOPED_JWT_SECRET", "").strip()
    if secret:
        return secret
    reason = (
        "SCOPED_JWT_SECRET is not set, so no refresh token can be signed the way "
        "the stack signs them."
    )
    if secrets_required():
        pytest.fail(f"{reason} The integration workflow sets it for every job.")
    pytest.skip(reason)


def _refresh_token(secret: str, user_id: str, org_id: str, issued: datetime, expires: datetime) -> str:
    """A refresh token signed exactly as Node's refreshTokenJwtGenerator signs one."""
    key = hmac.new(secret.encode(), _USER_ACTION_KEY_CONTEXT, hashlib.sha256).hexdigest()
    claims = {
        "userId": user_id,
        "orgId": org_id,
        "scopes": [_REFRESH_SCOPE],
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(claims, key, algorithm="HS256")


class TestExpiredRefreshToken:
    """Test list: an expired refresh token is refused.

    Refresh tokens live 30 days, so the expired one is signed here with the
    stack's test-only SCOPED_JWT_SECRET, as the storage suite signs its tokens.
    An unexpired token signed the same way must work first; otherwise the
    refusal could be a signing mismatch rather than the expiry.
    """

    def test_an_expired_refresh_token_is_refused(
        self,
        fresh_user: SecondUser,
        pipeshub_client: PipeshubClient,
        user_account_client: UserAccountClient,
    ) -> None:
        secret = _scoped_jwt_secret()
        now = datetime.now(timezone.utc)
        org_id = pipeshub_client.org_id

        live = _refresh_token(
            secret, fresh_user.user_id, org_id, now, now + timedelta(minutes=5)
        )
        control = user_account_client.refresh_token(live)
        assert control.status_code == 200, (
            f"A refresh token signed here, not yet expired, was refused (HTTP "
            f"{control.status_code}), so SCOPED_JWT_SECRET does not match the "
            "stack's and the expired case below would prove nothing."
        )

        expired = _refresh_token(
            secret, fresh_user.user_id, org_id, now - timedelta(hours=2), now - timedelta(hours=1)
        )
        response = user_account_client.refresh_token(expired)
        assert response.status_code == 401 and "accessToken" not in response.text, (
            f"A refresh token that expired an hour ago got HTTP "
            f"{response.status_code} (new access token returned: "
            f"{'accessToken' in response.text})."
        )
