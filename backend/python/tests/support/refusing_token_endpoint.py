"""An OAuth token endpoint that refuses every request with one fixed answer.

It stands in for ``aiohttp.ClientSession`` inside ``oauth_service``, so the real
``OAuthProvider`` sends the request and classifies the provider's answer.
"""

from __future__ import annotations

ENTRA_INVALID_CLIENT = (
    '{"error":"invalid_client","error_description":"AADSTS7000222: The provided client secret '
    'keys for app \'11111111-2222-3333-4444-555555555555\' are expired."}'
)
ENTRA_INTERACTION_REQUIRED = (
    '{"error":"interaction_required","error_description":"AADSTS50076: Due to a configuration '
    'change made by your administrator, you must use multi-factor authentication."}'
)


class RefusingTokenEndpoint:
    def __init__(self, status: int, body: str, content_type: str = "application/json; charset=utf-8") -> None:
        self.status = status
        self.body = body
        self.content_type = content_type
        self.requests: list[dict[str, str]] = []

    def client_session(self, *_: object, **__: object) -> _Session:
        return _Session(self)


class _Session:
    def __init__(self, endpoint: RefusingTokenEndpoint) -> None:
        self._endpoint = endpoint
        self.closed = False

    def post(self, _url: str, *, data: dict | None = None, json: dict | None = None, **_: object) -> _Response:
        self._endpoint.requests.append({k: str(v) for k, v in (data or json or {}).items()})
        return _Response(self._endpoint)

    async def close(self) -> None:
        self.closed = True


class _Response:
    def __init__(self, endpoint: RefusingTokenEndpoint) -> None:
        self.status = endpoint.status
        self.headers = {"Content-Type": endpoint.content_type}
        self._body = endpoint.body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def text(self) -> str:
        return self._body
