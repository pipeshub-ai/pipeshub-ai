"""Sign-in flows that go through email, read back from Mailpit.

The forgot-password link and the one-time sign-in code are the two flows the
stack can only complete by sending mail. The integration stack routes that mail
to Mailpit, so these tests request the email, read it from Mailpit's API, and
use what it carries exactly as a person clicking the link or typing the code
would.

Each class uses a throwaway account. The sign-in code class also turns on the
code method for the org alongside passwords, and puts the org's sign-in policy
back afterwards.

Not covered here: a reset link used after it expires. The link's 20-minute life
is fixed in code (``jwtGeneratorForForgotPasswordLink``), with no setting or
test hook to shorten it.
"""

from __future__ import annotations

import copy
from typing import Any, Iterator

import pytest
import requests

from helper import mailpit
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


def _sign_in_status(account: UserAccountClient, email: str, password: str) -> tuple[int, bool]:
    session_token = account.init_auth(email).headers.get("x-session-token", "")
    response = account.authenticate(session_token, email, password)
    return response.status_code, "accessToken" in response.text


class TestForgotPasswordLink:
    """Test list: password change by emailed link — works once, refused on reuse."""

    @pytest.fixture(scope="class")
    def used_link(
        self, mail_user: SecondUser, user_account_client: UserAccountClient
    ) -> dict[str, Any]:
        seen = mailpit.message_ids(mail_user.email)
        requested = user_account_client.forgot_password(mail_user.email)
        assert requested.status_code == 200, (
            f"Requesting a reset link failed: HTTP {requested.status_code}"
        )
        message = mailpit.wait_for_new_message(mail_user.email, RESET_SUBJECT, seen)
        token = mailpit.reset_link_token(message)
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
        assert status >= 400 and not token_returned, (
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
