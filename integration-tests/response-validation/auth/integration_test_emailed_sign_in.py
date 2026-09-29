"""Sign-in flows that go through email, read back from Mailpit.

The forgot-password link and the one-time sign-in code are the two flows the
stack can only complete by sending mail. The integration stack routes that mail
to Mailpit, so these tests request the email, read it from Mailpit's API, and
use what it carries exactly as a person clicking the link or typing the code
would.

The lockout tests live here too: the password and code counters are shared, so
checking that needs a sign-in code, and a lock sends a warning email. Five wrong
attempts lock an account; that is the product decision (the test list's ten is
being corrected).

Each class uses a throwaway account. Classes that need sign-in codes turn the
code method on for the org alongside passwords, and put the org's sign-in policy
back afterwards.

The expired-link test needs a stack whose links expire within two minutes. The
integration compose sets that once ``PASSWORD_RESET_LINK_EXPIRY`` exists
(#3681); until then, and on stacks with the 20-minute default, it skips.
"""

from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

import pytest
import requests

from helper import local_auth, mailpit
from helper.clients.auth_client import AuthClient, UserAccountClient
from helper.config import TEST_USER_PASSWORD
from helper.http.session_client import SessionClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser, create_second_user, delete_second_user

pytestmark = [pytest.mark.integration]

NEW_PASSWORD = "EmailedReset789!"
SECOND_PASSWORD = "SecondReset789!"
RESET_SUBJECT = "Reset your password"
SIGN_IN_CODE_SUBJECT = "OTP for Login"
LOCK_WARNING_SUBJECT = "Suspicious Login Attempt"
LOCKOUT_THRESHOLD = 5
# Longest link lifetime the expired-link test will wait out.
MAX_LINK_WAIT_SECONDS = 120
ANY_USER_ROUTE = "/api/v1/knowledgeBase"


@pytest.fixture(scope="module")
def mailbox(smtp_configured: None) -> None:
    """Mailpit must answer, or nothing about mail can be asserted.

    ``smtp_configured`` has already skipped runs with no mail server. A run
    that set one up but cannot read Mailpit is broken, not out of scope.
    """
    try:
        mailpit.message_ids("nobody@example.com")
    except mailpit.MailpitUnavailable as exc:
        pytest.fail(f"{exc}. Set MAILPIT_URL to Mailpit's web API.")


@pytest.fixture(scope="class")
def mail_user(pipeshub_client: PipeshubClient, mailbox: None) -> Iterator[SecondUser]:
    pipeshub_client._ensure_access_token()
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        delete_second_user(pipeshub_client, user, strict=True)


# How authenticate answers a wrong password, a locked account and an unknown
# email alike (WRONG_EMAIL_OR_PASSWORD).
WRONG_PASSWORD_STATUS = 400


def _sign_in_status(account: UserAccountClient, email: str, password: str) -> tuple[int, bool]:
    started = account.init_auth(email)
    session_token = started.headers.get("x-session-token", "")
    if started.status_code != 200 or not session_token:
        pytest.fail(
            f"initAuth for {email} answered HTTP {started.status_code} with "
            f"{'a' if session_token else 'no'} session token, so no password was tried."
        )
    response = account.authenticate(session_token, email, password)
    return response.status_code, "accessToken" in response.text


def _emailed_reset_token(account: UserAccountClient, email: str) -> str:
    seen = mailpit.message_ids(email)
    requested = account.forgot_password(email)
    assert requested.status_code == 200, (
        f"Requesting a reset link failed: HTTP {requested.status_code}"
    )
    message = mailpit.wait_for_new_message(email, RESET_SUBJECT, seen)
    return mailpit.reset_link_token(message)


class TestForgotPasswordLink:
    """Test list: password change by emailed link — works once, refused on reuse."""

    @pytest.fixture(scope="class")
    def used_link(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> dict[str, Any]:
        token = _emailed_reset_token(user_account_client, mail_user.email)
        first_use = user_account_client.reset_password_with_link(token, NEW_PASSWORD)
        return {"token": token, "first_status": first_use.status_code}

    def test_the_link_sets_a_new_password(
        self,
        used_link: dict[str, Any],
        mail_user: SecondUser,
        user_account_client: UserAccountClient,
    ) -> None:
        assert used_link["first_status"] == 200, (
            f"Using the emailed reset link failed: HTTP {used_link['first_status']}"
        )
        status, token_returned = _sign_in_status(
            user_account_client, mail_user.email, NEW_PASSWORD
        )
        assert status == 200 and token_returned, (
            f"The password set through the link does not sign in (HTTP {status})."
        )

    def test_the_old_password_stops_working(
        self,
        used_link: dict[str, Any],
        mail_user: SecondUser,
        user_account_client: UserAccountClient,
    ) -> None:
        if used_link["first_status"] != 200:
            pytest.fail("The link never worked, so the old password was never replaced.")
        status, token_returned = _sign_in_status(
            user_account_client, mail_user.email, TEST_USER_PASSWORD
        )
        assert status == WRONG_PASSWORD_STATUS and not token_returned, (
            f"The password from before the reset still signs in (HTTP {status})."
        )

    def test_the_same_link_is_refused_the_second_time(
        self,
        used_link: dict[str, Any],
        mail_user: SecondUser,
        user_account_client: UserAccountClient,
    ) -> None:
        if used_link["first_status"] != 200:
            pytest.fail("The link never worked once, so its reuse proves nothing.")
        second_use = user_account_client.reset_password_with_link(
            used_link["token"], SECOND_PASSWORD
        )
        assert second_use.status_code == 401, (
            f"A reset link that was already used worked again (HTTP "
            f"{second_use.status_code}). Anyone who later finds that email can "
            "take over the account."
        )
        status, token_returned = _sign_in_status(
            user_account_client, mail_user.email, NEW_PASSWORD
        )
        assert status == 200 and token_returned, (
            f"After the refused reuse, the password set by the first use no "
            f"longer signs in (HTTP {status}), so the reuse changed something."
        )


class TestExpiredResetLink:
    """Test list: an expired reset link is refused."""

    def test_an_expired_link_is_refused(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> None:
        token = _emailed_reset_token(user_account_client, mail_user.email)
        claims = local_auth.jwt_claims(token)
        lifetime = int(claims.get("exp", 0)) - int(claims.get("iat", 0))
        if not 0 < lifetime <= MAX_LINK_WAIT_SECONDS:
            pytest.skip(
                f"Reset links on this stack last {lifetime}s; this test waits for "
                f"expiry only when that is at most {MAX_LINK_WAIT_SECONDS}s "
                "(PASSWORD_RESET_LINK_EXPIRY, 60s in the integration compose)."
            )
        # Two seconds past expiry, measured against the link's own clock.
        time.sleep(max(0.0, int(claims["exp"]) - time.time()) + 2)

        response = user_account_client.reset_password_with_link(token, NEW_PASSWORD)
        assert response.status_code == 401, (
            f"A reset link used after it expired got HTTP {response.status_code}."
        )
        status, token_returned = _sign_in_status(
            user_account_client, mail_user.email, TEST_USER_PASSWORD
        )
        assert status == 200 and token_returned, (
            f"After the expired link was refused, the original password no longer "
            f"signs in (HTTP {status}), so the refused reset changed something."
        )


RESET_LINK_RACE = (
    "A reset link is single-use only through a comparison of its issue time with "
    "the account's latest PASSWORD_CHANGED record (scopedTokenValidator in "
    "auth.middleware.ts). That record is written at the end of updatePassword, "
    "after a user lookup and two bcrypt operations, and nothing claims the link "
    "atomically, so two requests arriving before it is written both succeed. "
    "Fixed by #3681: remove this mark when it merges."
)


class TestResetLinkUsedTwiceAtOnce:
    """The sequential reuse check above cannot see the window before the first
    use is recorded; two simultaneous uses of one unused link can."""

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=RESET_LINK_RACE)
    def test_only_one_of_two_simultaneous_uses_succeeds(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> None:
        token = _emailed_reset_token(user_account_client, mail_user.email)
        start_together = threading.Barrier(2)

        def use(password: str) -> int:
            start_together.wait(timeout=30)
            return user_account_client.reset_password_with_link(token, password).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(use, [NEW_PASSWORD, SECOND_PASSWORD]))

        if 200 not in statuses:
            pytest.fail(
                f"Neither simultaneous use worked (HTTP {statuses}), so this says "
                "nothing about single use."
            )
        assert statuses.count(200) == 1, (
            f"Two simultaneous uses of one reset link both succeeded (HTTP "
            f"{statuses}). A link meant to work once worked twice."
        )


def _has_otp(steps: list[dict[str, Any]]) -> bool:
    return any(
        method.get("type") == "otp"
        for step in steps
        for method in step.get("allowedMethods") or []
    )


@pytest.fixture(scope="class")
def otp_allowed(user_session_client: SessionClient, mailbox: None) -> Iterator[None]:
    """Offer sign-in codes next to passwords in the org's first sign-in step.

    Passwords stay allowed throughout, so other suites signing in meanwhile are
    unaffected. The previous policy is put back afterwards, and a failure to do
    so is raised rather than logged: a policy left changed alters how every
    later test's sign-in behaves.
    """
    org_auth = AuthClient(user_session_client)
    current = org_auth.get_auth_methods()
    assert current.status_code == 200, (
        f"Reading the org's sign-in policy failed: HTTP {current.status_code}"
    )
    original = current.json()["authMethods"]
    # A code opens a session only when it is the last step; with more steps,
    # authenticate answers with the next step instead of a session.
    if len(original) != 1:
        pytest.skip(
            f"The org's sign-in policy has {len(original)} steps; these tests "
            "need a single-step policy to finish a sign-in with a code."
        )
    if _has_otp(original):
        yield
        return

    updated = copy.deepcopy(original)
    updated[0]["allowedMethods"].append({"type": "otp"})
    changed = org_auth.update_auth_method(json={"authMethod": updated})
    assert changed.status_code == 200, (
        f"Allowing sign-in codes failed: HTTP {changed.status_code} {changed.text[:200]}"
    )
    try:
        yield
    finally:
        restored = org_auth.update_auth_method(json={"authMethod": original})
        if restored.status_code != 200:
            raise RuntimeError(
                "Could not restore the org's sign-in policy after the sign-in "
                f"code tests: HTTP {restored.status_code} {restored.text[:200]}"
            )


def _request_code(account: UserAccountClient, email: str) -> str:
    seen = mailpit.message_ids(email)
    requested = account.request_login_otp(email)
    assert requested.status_code == 200, (
        f"Requesting a sign-in code failed: HTTP {requested.status_code}"
    )
    message = mailpit.wait_for_new_message(email, SIGN_IN_CODE_SUBJECT, seen)
    return mailpit.sign_in_code(message)


def _code_session(account: UserAccountClient, email: str) -> str:
    started = account.init_auth(email)
    assert started.status_code == 200, f"initAuth failed: HTTP {started.status_code}"
    assert "otp" in (started.json().get("allowedMethods") or []), (
        "The sign-in session does not offer codes, though the org policy was "
        "just changed to allow them."
    )
    return started.headers.get("x-session-token", "")


@pytest.mark.usefixtures("otp_allowed")
class TestSignInCode:
    """Test list: OTP login — the right code signs in, a wrong one is refused."""

    def test_a_wrong_code_is_refused(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> None:
        code = _request_code(user_account_client, mail_user.email)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        session_token = _code_session(user_account_client, mail_user.email)
        response = user_account_client.authenticate_with_otp(
            session_token, mail_user.email, wrong
        )
        assert response.status_code == 401 and "accessToken" not in response.text, (
            f"A wrong sign-in code got HTTP {response.status_code} (session "
            f"returned: {'accessToken' in response.text})."
        )

    def test_the_right_code_signs_in(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> None:
        code = _request_code(user_account_client, mail_user.email)
        session_token = _code_session(user_account_client, mail_user.email)
        response = user_account_client.authenticate_with_otp(
            session_token, mail_user.email, code
        )
        assert response.status_code == 200, (
            f"The emailed sign-in code was refused: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        access_token = response.json().get("accessToken")
        assert access_token, "Signing in with the code returned no access token."
        status = requests.get(
            f"{mail_user.base_url}{ANY_USER_ROUTE}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=mail_user.timeout,
        ).status_code
        assert status == 200, (
            f"The session opened with a sign-in code does not work (HTTP {status})."
        )


def _wrong_password(account: UserAccountClient, email: str, attempt: int) -> None:
    status, token_returned = _sign_in_status(account, email, f"DefinitelyWrong{attempt}!")
    assert status == WRONG_PASSWORD_STATUS and not token_returned, (
        f"Wrong password {attempt} was accepted (HTTP {status})."
    )


def _wrong_code(account: UserAccountClient, email: str, real_code: str, attempt: int) -> None:
    wrong = f"{(int(real_code) + attempt) % 1_000_000:06d}"
    session_token = _code_session(account, email)
    response = account.authenticate_with_otp(session_token, email, wrong)
    assert response.status_code == 401 and "accessToken" not in response.text, (
        f"Wrong code {attempt} got HTTP {response.status_code}."
    )


class TestLockoutThreshold:
    """Test list: wrong passwords lock the account; the threshold is five."""

    def test_the_fifth_wrong_password_locks_and_the_fourth_does_not(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> None:
        email = mail_user.email
        for attempt in range(1, LOCKOUT_THRESHOLD):
            _wrong_password(user_account_client, email, attempt)
        # A successful sign-in also resets the count, so the next loop starts at zero.
        status, token_returned = _sign_in_status(user_account_client, email, TEST_USER_PASSWORD)
        assert status == 200 and token_returned, (
            f"{LOCKOUT_THRESHOLD - 1} wrong passwords locked the account: the right "
            f"one then got HTTP {status}."
        )

        seen = mailpit.message_ids(email)
        for attempt in range(1, LOCKOUT_THRESHOLD + 1):
            _wrong_password(user_account_client, email, attempt)
        status, token_returned = _sign_in_status(user_account_client, email, TEST_USER_PASSWORD)
        assert status == WRONG_PASSWORD_STATUS and not token_returned, (
            f"{LOCKOUT_THRESHOLD} wrong passwords did not lock the account: the "
            f"right one then got HTTP {status}."
        )
        mailpit.wait_for_new_message(email, LOCK_WARNING_SUBJECT, seen)


@pytest.mark.usefixtures("otp_allowed")
class TestLockoutCountersShared:
    """Wrong codes and wrong passwords add up to the same lock."""

    def test_wrong_codes_and_wrong_passwords_count_together(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> None:
        email = mail_user.email
        wrong_codes = 3
        real_code = _request_code(user_account_client, email)
        for attempt in range(1, wrong_codes + 1):
            _wrong_code(user_account_client, email, real_code, attempt)
        for attempt in range(1, LOCKOUT_THRESHOLD - wrong_codes + 1):
            _wrong_password(user_account_client, email, attempt)

        status, token_returned = _sign_in_status(user_account_client, email, TEST_USER_PASSWORD)
        assert status == WRONG_PASSWORD_STATUS and not token_returned, (
            f"{wrong_codes} wrong codes and {LOCKOUT_THRESHOLD - wrong_codes} wrong "
            f"passwords did not lock the account (the right password got HTTP "
            f"{status}), so the two are counted separately."
        )
