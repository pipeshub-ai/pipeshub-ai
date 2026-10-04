"""A token endpoint that takes client credentials only as ``client_secret_basic``.

It stands in for ``aiohttp.ClientSession`` inside ``oauth_service``, so the real
``OAuthProvider`` builds and sends the request. It follows Airtable's OAuth
reference, which Zoom's matches for apps with a secret: the Basic header is
required when the app has a secret and forbidden when it does not, a secret in
the body is refused, and a public (PKCE) client names itself with ``client_id``
in the body.
"""

from __future__ import annotations

import base64
from json import dumps
from typing import Any

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401


class FakeBasicAuthTokenEndpoint:
    def __init__(self, token_url: str, client_id: str, client_secret: str | None) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.requests: list[tuple[dict[str, str], dict[str, str]]] = []

    def client_session(self, *_: object, **__: object) -> _Session:
        return _Session(self)

    def answer(self, url: str, headers: dict[str, str], form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if url != self.token_url:
            raise AssertionError(f"unexpected token URL {url}")
        self.requests.append((dict(headers), dict(form)))
        authorization = headers.get("Authorization")

        if "client_secret" in form:
            return HTTP_UNAUTHORIZED, {"error": "invalid_client", "error_description": "client_secret must not be sent in the body"}
        if self.client_secret:
            expected = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            if authorization != f"Basic {expected}":
                return HTTP_UNAUTHORIZED, {"error": "invalid_client"}
        else:
            if authorization is not None:
                return HTTP_BAD_REQUEST, {"error": "invalid_request", "error_description": "Authorization header is forbidden without a client_secret"}
            if form.get("client_id") != self.client_id:
                return HTTP_UNAUTHORIZED, {"error": "invalid_client"}

        grant_type = form.get("grant_type")
        if grant_type == "authorization_code" and form.get("code"):
            n = len(self.requests)
        elif grant_type == "refresh_token" and form.get("refresh_token"):
            n = len(self.requests)
        else:
            return HTTP_BAD_REQUEST, {"error": "invalid_grant"}
        return HTTP_OK, {
            "access_token": f"access-{n}",
            "refresh_token": f"refresh-{n}",
            "token_type": "Bearer",
            "expires_in": 3600,
        }


class _Session:
    def __init__(self, endpoint: FakeBasicAuthTokenEndpoint) -> None:
        self._endpoint = endpoint
        self.closed = False

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict | None = None,
        json: dict | None = None,
        **_: object,
    ) -> _Response:
        form = {k: str(v) for k, v in (data or json or {}).items()}
        return _Response(*self._endpoint.answer(url, headers or {}, form))

    async def close(self) -> None:
        self.closed = True


class _Response:
    headers = {"Content-Type": "application/json"}

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._body

    async def text(self) -> str:
        return dumps(self._body)
