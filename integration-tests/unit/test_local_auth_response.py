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
