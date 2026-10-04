"""Slack's ``oauth.v2.access`` token endpoint, for tests of token rotation.

It stands in for ``aiohttp.ClientSession`` inside ``oauth_service``, so the real
``OAuthProvider`` builds and sends the request and reads the answer. Like Slack,
it answers every failure with HTTP 200 and ``ok: false``, and each refresh token
works once: a refresh returns a new access token and a new refresh token, and
the old refresh token stops working.
"""

from __future__ import annotations

from json import dumps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

TOKEN_URL = "https://slack.com/api/oauth.v2.access"
ACCESS_TOKEN_LIFETIME = 43200


class FakeSlackOAuth:
    def __init__(
        self,
        refresh_token: str | None,
        *,
        nested_under_authed_user: bool = False,
        on_issue: Callable[[str], None] | None = None,
    ) -> None:
        self.live_refresh_token = refresh_token
        self.nested_under_authed_user = nested_under_authed_user
        self.on_issue = on_issue
        self.requests: list[dict[str, str]] = []
        self.issued: list[tuple[str, str]] = []
        # When set, answers wait for it, so a test can line up concurrent refreshes.
        self.hold: asyncio.Event | None = None

    def client_session(self, *_: object, **__: object) -> _Session:
        return _Session(self)

    async def answer(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        if url != TOKEN_URL:
            raise AssertionError(f"unexpected token URL {url}")
        self.requests.append(dict(form))
        if self.hold is not None:
            await self.hold.wait()
        if form.get("grant_type") != "refresh_token":
            return {"ok": False, "error": "invalid_grant_type"}
        if not form.get("client_id") or not form.get("client_secret"):
            return {"ok": False, "error": "invalid_client_id"}
        if form.get("refresh_token") != self.live_refresh_token:
            return {"ok": False, "error": "invalid_refresh_token"}

        n = len(self.issued) + 1
        access_token, refresh_token = f"xoxe.xoxp-1-renewed-{n}", f"xoxe-1-renewed-{n}"
        self.issued.append((access_token, refresh_token))
        self.live_refresh_token = refresh_token
        if self.on_issue is not None:
            self.on_issue(access_token)

        tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": ACCESS_TOKEN_LIFETIME,
            "token_type": "user",
            "scope": "channels:read,channels:history",
        }
        if self.nested_under_authed_user:
            return {"ok": True, "app_id": "A0APP", "authed_user": {"id": "U0ALICE", **tokens}, "team": {"id": "T0ACME"}}
        return {"ok": True, "app_id": "A0APP", **tokens, "team": {"id": "T0ACME"}}


class _Session:
    def __init__(self, oauth: FakeSlackOAuth) -> None:
        self._oauth = oauth
        self.closed = False

    def post(self, url: str, *, data: dict | None = None, json: dict | None = None, **_: object) -> _Response:
        form = {k: str(v) for k, v in (data or json or {}).items()}
        return _Response(self._oauth, url, form)

    async def close(self) -> None:
        self.closed = True


class _Response:
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}

    def __init__(self, oauth: FakeSlackOAuth, url: str, form: dict[str, str]) -> None:
        self._oauth = oauth
        self._url = url
        self._form = form
        self._body: dict[str, Any] = {}

    async def __aenter__(self) -> _Response:
        self._body = await self._oauth.answer(self._url, self._form)
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._body

    async def text(self) -> str:
        return dumps(self._body)
