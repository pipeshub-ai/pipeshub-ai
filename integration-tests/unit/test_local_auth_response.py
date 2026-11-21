"""A malformed authenticate response fails with a clear message, never a token."""

from __future__ import annotations

import pytest

from helper import local_auth

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, body: object, status: int = 200) -> None:
        self._body = body
        self.status_code = status
        self.text = str(body)
        self.headers = {"x-session-token": "session"}

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _serve(monkeypatch, authenticate_body: object) -> None:
    def post(url: str, **_kwargs):
        return _Response({}) if url.endswith("/initAuth") else _Response(authenticate_body)

    monkeypatch.setattr(local_auth.requests, "post", post)


def test_both_tokens_come_back(monkeypatch) -> None:
    # A payload with userId, so no org switch is attempted.
    access = "h.eyJ1c2VySWQiOiJ1MSJ9.s"
    _serve(monkeypatch, {"accessToken": access, "refreshToken": "r1", "extra": 1})
    assert local_auth.log_in_with_refresh_token("http://x", "a@b.c", "pw") == (access, "r1")


def test_a_two_step_sign_in_returns_the_refresh_token_of_the_switch(monkeypatch) -> None:
    # authenticate answers an email-only token (no userId) and no refresh token;
    # the switch to the org issues both.
    email_only, session = "h.eyJlbWFpbCI6ImFAYi5jIn0.s", "h.eyJ1c2VySWQiOiJ1MSJ9.s"

    def post(url: str, **_kwargs):
        if url.endswith("/initAuth"):
            return _Response({})
        if url.endswith("/auth/token/switch"):
            return _Response({"accessToken": session, "refreshToken": "r-switch"})
        return _Response({"accessToken": email_only, "orgId": "o1"})

    monkeypatch.setattr(local_auth.requests, "post", post)
    assert local_auth.log_in_with_refresh_token("http://x", "a@b.c", "pw") == (session, "r-switch")


def test_finish_sign_in_keeps_a_one_step_reply_and_switches_a_two_step_one(monkeypatch) -> None:
    one_step = {"accessToken": "h.eyJ1c2VySWQiOiJ1MSJ9.s", "refreshToken": "r1"}
    assert local_auth.finish_sign_in("http://x", one_step) == ("h.eyJ1c2VySWQiOiJ1MSJ9.s", "r1")

    def post(url: str, **_kwargs):
        assert url == "http://x/api/v1/auth/token/switch"
        return _Response({"accessToken": "h.eyJ1c2VySWQiOiJ1MiJ9.s", "refreshToken": "r2"})

    monkeypatch.setattr(local_auth.requests, "post", post)
    two_step = {"accessToken": "h.eyJlbWFpbCI6ImFAYi5jIn0.s", "orgId": "o1"}
    assert local_auth.finish_sign_in("http://x/", two_step) == ("h.eyJ1c2VySWQiOiJ1MiJ9.s", "r2")


@pytest.mark.parametrize(
    "body, named",
    [
        ({"refreshToken": "secret-refresh"}, "accessToken"),
        ({"accessToken": ""}, "accessToken"),
        ({"accessToken": {"nested": True}}, "accessToken"),
        ([1, 2], "body"),
        (ValueError("no json"), "not JSON"),
    ],
)
def test_a_malformed_response_is_a_clear_error(monkeypatch, body: object, named: str) -> None:
    _serve(monkeypatch, body)
    with pytest.raises(RuntimeError, match="unusable response") as caught:
        local_auth.log_in_with_refresh_token("http://x", "a@b.c", "pw")
    assert named in str(caught.value)
    assert "secret-refresh" not in str(caught.value)
