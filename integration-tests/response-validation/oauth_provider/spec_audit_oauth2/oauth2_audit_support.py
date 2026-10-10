"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/oauth2."""

from __future__ import annotations

import base64
import datetime
import hashlib
import secrets
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import requests
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.mcp_oauth import (
    OAuthApp,
    authorization_code_token,
    create_oauth_app,
    delete_oauth_app,
)
from helper.second_user import SecondUser
from strict_openapi import _escape, _schema_errors, _spec

OAUTH2_BASE = "/api/v1/oauth2"

# The instance-wide public client; the only one an API caller can use for the
# device grant, since POST /oauth-clients does not accept the device_code grant.
FIRST_PARTY_DEVICE_CLIENT_ID = "pipeshub-agent"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
UNKNOWN_CLIENT_ID = "spec-audit-no-such-client"

# "0" is outside the alphabet user codes are drawn from, so this is never issued.
UNKNOWN_USER_CODE = "0000-0000"
# Longer than the 32 characters the validator allows.
MALFORMED_USER_CODE = "A" * 33

MALFORMED_ACCESS_TOKEN = "not-a-jwt"
OIDC_SCOPES = ("openid", "profile", "email")
DCR_REDIRECT_URI = "http://localhost/callback"

DEVICE_CODES_COLLECTION = "oauthDeviceCodes"
OAUTH_APPS_COLLECTION = "oauthApps"
ACCESS_TOKENS_COLLECTION = "oauthAccessTokens"
REFRESH_TOKENS_COLLECTION = "oauthRefreshTokens"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

# Grants an app made through /oauth-clients can have; device_code is not among them.
APP_GRANT_TYPES = ["authorization_code", "refresh_token", "client_credentials"]


@dataclass
class DeviceGrant:
    """One pending device authorization; ``response`` is the 200 that created it."""

    device_code: str
    user_code: str
    response: requests.Response


StartDeviceGrant = Callable[..., DeviceGrant]


class OAuth2Client(APIClient):
    """Client for /api/v1/oauth2, acting as whoever the wrapped HTTP client is."""

    BASE = OAUTH2_BASE

    def authorize(
        self, *, auth: bool = True, headers: dict[str, str] | None = None, **params: Any
    ) -> requests.Response:
        """GET /authorize without following the redirect it answers unauthenticated callers with."""
        return self.get(
            "/authorize", auth=auth, params=params, headers=headers, allow_redirects=False
        )

    def authorize_consent(
        self, *, auth: bool = True, form: bool = False, **body: Any
    ) -> requests.Response:
        # The session client defaults to a JSON Content-Type, so a form body names its own.
        if form:
            return self.post("/authorize", auth=auth, data=body, headers=FORM_HEADERS)
        return self.post("/authorize", auth=auth, json=body)

    def token(self, *, form: bool = False, **body: Any) -> requests.Response:
        if form:
            return self.post("/token", auth=False, data=body)
        return self.post("/token", auth=False, json=body)

    def token_with_basic_auth(
        self, client_id: str, client_secret: str, **body: Any
    ) -> requests.Response:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        return self.post(
            "/token", auth=False, json=body, headers={"Authorization": f"Basic {basic}"}
        )

    def revoke(self, *, form: bool = False, **body: Any) -> requests.Response:
        if form:
            return self.post("/revoke", auth=False, data=body)
        return self.post("/revoke", auth=False, json=body)

    def introspect(self, *, form: bool = False, **body: Any) -> requests.Response:
        if form:
            return self.post("/introspect", auth=False, data=body)
        return self.post("/introspect", auth=False, json=body)

    def userinfo(self, access_token: str | None = None) -> requests.Response:
        """GET /userinfo with exactly this bearer token, or with no Authorization header."""
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
        return self.get("/userinfo", auth=False, headers=headers)

    def register(self, *, form: bool = False, **metadata: Any) -> requests.Response:
        if form:
            return self.post("/register", auth=False, data=metadata)
        return self.post("/register", auth=False, json=metadata)

    def device_authorization(self, *, form: bool = False, **body: Any) -> requests.Response:
        if form:
            return self.post("/device_authorization", auth=False, data=body)
        return self.post("/device_authorization", auth=False, json=body)

    def device_verify(
        self, *, auth: bool = True, form: bool = False, **body: Any
    ) -> requests.Response:
        if form:
            return self.post("/device/verify", auth=auth, data=body, headers=FORM_HEADERS)
        return self.post("/device/verify", auth=auth, json=body)

    def device_consent(
        self, *, auth: bool = True, form: bool = False, **body: Any
    ) -> requests.Response:
        if form:
            return self.post("/device/consent", auth=auth, data=body, headers=FORM_HEADERS)
        return self.post("/device/consent", auth=auth, json=body)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an oauth2 route with the non-admin member's session JWT; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{OAUTH2_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


@contextmanager
def user_bound_access_token(
    base_url: str, session_jwt: str, scopes: tuple[str, ...] = OIDC_SCOPES
) -> Iterator[str]:
    """An OAuth access token issued to the session's user, from a throwaway app.

    A client-credentials token (what ``pipeshub_client`` holds) carries no user,
    so /userinfo answers it with 401; only a user-bound token reaches the 200.
    """
    app = create_oauth_app(
        base_url,
        session_jwt,
        f"spec-audit-oauth2-{uuid.uuid4().hex[:8]}",
        scopes=list(scopes),
        grant_types=["authorization_code", "refresh_token"],
    )
    try:
        tokens = authorization_code_token(base_url, session_jwt, app)
        yield tokens["access_token"]
    finally:
        delete_oauth_app(base_url, session_jwt, app.id)


def _delete_documents(collection: str, query: dict[str, Any]) -> None:
    """No route removes these rows; they outlive the test if Mongo cannot be reached."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client[MONGO_DB_NAME][collection].delete_many(query)
    except Exception as exc:  # noqa: BLE001 - teardown must not mask the test result
        warnings.warn(f"could not clean {collection} {query}: {exc}", stacklevel=2)
    finally:
        client.close()


def delete_device_grants(user_codes: list[str]) -> None:
    """Remove device authorizations, pending or approved, by the user_code shown to the user."""
    stored = [code.replace("-", "").upper() for code in user_codes]
    if stored:
        _delete_documents(DEVICE_CODES_COLLECTION, {"userCode": {"$in": stored}})


def delete_dynamic_client(client_id: str) -> None:
    """Remove a client POST /register created; /oauth-clients hides dynamic clients."""
    _delete_documents(
        OAUTH_APPS_COLLECTION, {"clientId": client_id, "isDynamic": True}
    )


def pkce_pair() -> tuple[str, str]:
    """RFC 7636 verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_params(redirect_url: str) -> dict[str, str]:
    """Query parameters of the redirectUrl the authorize routes answer with."""
    return {k: v[0] for k, v in parse_qs(urlsplit(redirect_url).query).items()}


def create_public_app(base_url: str, session_jwt: str, scopes: tuple[str, ...]) -> OAuthApp:
    """A non-confidential app: the authorize routes insist on PKCE for these."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth-clients",
        headers={"Authorization": f"Bearer {session_jwt}"},
        json={
            "name": f"spec-audit-oauth2-public-{uuid.uuid4().hex[:8]}",
            "allowedGrantTypes": ["authorization_code", "refresh_token"],
            "allowedScopes": list(scopes),
            "redirectUris": [DCR_REDIRECT_URI],
            "isConfidential": False,
        },
        timeout=30,
    )
    assert resp.status_code in (200, 201), resp.text[:500]
    app = resp.json()["app"]
    return OAuthApp(
        id=str(app.get("id") or app.get("_id")),
        client_id=app["clientId"],
        client_secret=app.get("clientSecret", ""),
        scopes=list(scopes),
        redirect_uri=DCR_REDIRECT_URI,
    )


def consent_body(app: OAuthApp, **overrides: Any) -> dict[str, Any]:
    """A POST /authorize body for ``app`` that grants consent to ``openid``."""
    body: dict[str, Any] = {
        "client_id": app.client_id,
        "redirect_uri": app.redirect_uri,
        "scope": "openid",
        "state": f"st-{uuid.uuid4().hex[:8]}",
        "consent": "granted",
    }
    body.update(overrides)
    return body


def authorize_params(app: OAuthApp, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "response_type": "code",
        "client_id": app.client_id,
        "redirect_uri": app.redirect_uri,
        "scope": "openid",
        "state": f"st-{uuid.uuid4().hex[:8]}",
    }
    params.update(overrides)
    return params


def authorization_code(
    session_client: OAuth2Client, app: OAuthApp, scope: str = "openid"
) -> tuple[str, str]:
    """A fresh authorization code for ``app`` and its PKCE verifier."""
    verifier, challenge = pkce_pair()
    resp = session_client.authorize_consent(
        **consent_body(app, scope=scope, code_challenge=challenge, code_challenge_method="S256")
    )
    assert resp.status_code == 200, resp.text[:500]
    return redirect_params(resp.json()["redirectUrl"])["code"], verifier


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def delete_issued_tokens(*tokens: str) -> None:
    """Remove tokens the test minted for the admin through a client that cannot revoke them.

    POST /revoke cannot authenticate a public client, so the device grant's tokens stay
    live otherwise.
    """
    hashes = [_hash(t) for t in tokens if t]
    if hashes:
        _delete_documents(ACCESS_TOKENS_COLLECTION, {"tokenHash": {"$in": hashes}})
        _delete_documents(REFRESH_TOKENS_COLLECTION, {"tokenHash": {"$in": hashes}})


def insert_device_grant(
    user_code: str, *, client_id: str = FIRST_PARTY_DEVICE_CLIENT_ID, expires_in: int = 300
) -> str:
    """A pending device grant written straight to Mongo; returns its device_code.

    The API only creates grants that are live and belong to an active app, so an
    expired one, or one whose app is gone, has to be made by hand.
    """
    device_code = f"spec-audit-{uuid.uuid4().hex}"
    now = datetime.datetime.now(datetime.timezone.utc)
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client[MONGO_DB_NAME][DEVICE_CODES_COLLECTION].insert_one(
            {
                "deviceCodeHash": _hash(device_code),
                "userCode": user_code.replace("-", "").upper(),
                "clientId": client_id,
                "scopes": ["kb:read"],
                "status": "pending",
                "interval": 5,
                "expiresAt": now + datetime.timedelta(seconds=expires_in),
                "createdAt": now,
                "updatedAt": now,
            }
        )
    finally:
        client.close()
    return device_code


def random_user_code() -> str:
    """A code from the issuing alphabet that no live grant is likely to hold."""
    alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{code[:4]}-{code[4:]}"


def assert_form_schema_refuses(resp: requests.Response, spec_path: str) -> None:
    """The gate reads only JSON bodies, so a validator refusal of a form body is checked here.

    A key sent once becomes a string and a repeated key a list, as Express's body parser does.
    """
    method = (resp.request.method or "").lower()
    body = resp.request.body
    text = body.decode() if isinstance(body, bytes) else body or ""
    data = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(text, keep_blank_values=True).items()}
    pointer = (
        f"#/paths/{_escape(spec_path)}/{method}/requestBody/content/"
        f"{_escape('application/x-www-form-urlencoded')}/schema"
    )
    _, registry = _spec()
    assert _schema_errors(registry, pointer, data), (
        f"{method.upper()} {spec_path}: the API refused the form body {data} "
        "but the spec's form schema allows it"
    )
