"""Sign in with a password, then refresh the session."""

import os

from pipeshub_sdk import Pipeshub, models


def test_handwritten_sign_in(pipeshub: Pipeshub):
    email = os.environ["PIPESHUB_TEST_USER_EMAIL"]

    init = pipeshub.user_account.init_auth(request={"email": email})
    assert "password" in init.result.allowed_methods
    [session_token] = init.headers["x-session-token"]

    signed_in = pipeshub.user_account.authenticate(
        x_session_token=session_token,
        method="password",
        email=email,
        credentials={"password": os.environ["PIPESHUB_TEST_USER_PASSWORD"]},
    )
    assert isinstance(signed_in, models.AuthenticateFinalResponse), signed_in
    assert signed_in.access_token

    refreshed = pipeshub.user_account.refresh_token(
        security={"scoped_token": signed_in.refresh_token}
    )
    assert refreshed.access_token
    assert refreshed.user.email == email
