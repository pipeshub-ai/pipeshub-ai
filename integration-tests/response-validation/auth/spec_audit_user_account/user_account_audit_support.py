"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/userAccount.

The org-auth-config and SAML audits import the account, policy and settings helpers from here.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs

import bcrypt
import jwt
import redis
import requests
from bson import ObjectId
from bson.errors import InvalidId
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import MongoClient

from helper import mailpit
from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.pipeshub_client import PipeshubClient

USER_ACCOUNT_BASE = "/api/v1/userAccount"
INIT_AUTH_ROUTE = f"{USER_ACCOUNT_BASE}/initAuth"
AUTHENTICATE_ROUTE = f"{USER_ACCOUNT_BASE}/authenticate"
OTP_GENERATE_ROUTE = f"{USER_ACCOUNT_BASE}/login/otp/generate"
PASSWORD_RESET_ROUTE = f"{USER_ACCOUNT_BASE}/password/reset"
REFRESH_TOKEN_ROUTE = f"{USER_ACCOUNT_BASE}/refresh/token"
LOGOUT_ROUTE = f"{USER_ACCOUNT_BASE}/logout/manual"
PASSWORD_RESET_TOKEN_ROUTE = f"{USER_ACCOUNT_BASE}/password/reset/token"
PASSWORD_FORGOT_ROUTE = f"{USER_ACCOUNT_BASE}/password/forgot"
OAUTH_EXCHANGE_ROUTE = f"{USER_ACCOUNT_BASE}/oauth/exchange"
VALIDATE_EMAIL_CHANGE_ROUTE = f"{USER_ACCOUNT_BASE}/validateEmailChange"

ORG_AUTH_CONFIG_BASE = "/api/v1/orgAuthConfig"
AUTH_METHODS_ROUTE = f"{ORG_AUTH_CONFIG_BASE}/authMethods"
UPDATE_AUTH_METHOD_ROUTE = f"{ORG_AUTH_CONFIG_BASE}/updateAuthMethod"

VALIDATE_EMAIL_SCOPE = "email:validate"
PASSWORD_RESET_SCOPE = "password:reset"
TOKEN_REFRESH_SCOPE = "token:refresh"
# A real scope validateEmailChange does not accept.
OTHER_SCOPE = PASSWORD_RESET_SCOPE
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

MISSING_USER_ID = "0123456789abcdef01234567"
MALFORMED_USER_ID = "not-an-object-id"

ACCOUNT_PASSWORD = "SpecAudit#Pass123"
NEW_PASSWORD = "SpecAudit#Pass456"
WEAK_PASSWORD = "weak"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
INTERNAL_ERROR = "INTERNAL_ERROR"
VALIDATION_ERROR = "VALIDATION_ERROR"
BAD_REQUEST = "HTTP_BAD_REQUEST"
UNAUTHORIZED = "HTTP_UNAUTHORIZED"

# Messages in backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts.
WRONG_EMAIL_OR_PASSWORD = (
    "The email or password is incorrect. Check both and try again, or use Forgot "
    "password to set a new password."
)
WRONG_SIGN_IN_CODE = (
    "That sign-in code isn't right. Check the most recent code in your email, or "
    "request a new one."
)
SIGN_IN_CODE_REQUESTED = (
    "If that email can sign in with a code, one is being sent. It works for 10 minutes. "
    "If nothing arrives, check your spam folder or try again later."
)
OAUTH_SIGN_IN_FAILED = (
    "Sign-in with your identity provider didn't complete. Try again; if it keeps "
    "happening, ask your admin to check the sign-in settings."
)
ACCOUNT_NO_LONGER_ACTIVE = "Your account is no longer active. Contact your admin."
SESSION_NO_LONGER_VALID = "Your session is no longer valid. Please sign in again."
SIGN_IN_METHOD_NOT_ALLOWED = (
    "That sign-in method isn't turned on for this step. Go back to the sign-in page "
    "and use one of the options it shows, or ask your admin which sign-in methods are enabled."
)
NO_AUTHORIZATION_HEADER = "Authorization header not found"
ADMIN_ACCESS_REQUIRED = "You need admin access to do this. Ask an admin in your organisation."

PASSWORD_ONLY: list[dict[str, Any]] = [{"order": 1, "allowedMethods": [{"type": "password"}]}]

# Mirrors USER_ACTION_KEY_CONTEXT in backend/nodejs/apps/src/libs/utils/jwtKeys.ts.
_USER_ACTION_KEY_CONTEXT = "pipeshub/jwt/user-action/v1"

# Where the Node configuration store keeps its keys in Redis (RedisDistributedKeyValueStore.ts).
_KV_PREFIX = "pipeshub:kv:"
_KV_INVALIDATION_CHANNEL = "pipeshub:cache:invalidate"
OAUTH_SETTING = "/services/auth/oauth"
SSO_SETTING = "/services/auth/sso"
OAUTH_CONFIG_PATH = "/api/v1/configurationManager/authConfig/oauth"
SSO_CONFIG_PATH = "/api/v1/configurationManager/authConfig/sso"

MintEmailChangeToken = Callable[..., str]
MintUserToken = Callable[..., str]


@dataclass(frozen=True)
class DisposableMember:
    """A non-admin account made for one test; ``email`` is the one it was created with."""

    user_id: str
    org_id: str
    email: str


@dataclass(frozen=True)
class Account(DisposableMember):
    """A disposable member that can sign in with ``password``."""

    password: str = ACCOUNT_PASSWORD


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def user_action_key(secret: str) -> str:
    """The key Node signs user-held tokens with (deriveUserActionSecret), not the raw secret."""
    return hmac.new(
        secret.encode(), _USER_ACTION_KEY_CONTEXT.encode(), hashlib.sha256
    ).hexdigest()


def mint_scoped_token(
    key: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 1200,
    **claims: Any,
) -> str:
    """HS256 token signed with ``key`` as given; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, key, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def unused_email(prefix: str = "spec-audit-email") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@test-pipeshub.com"


def oauth_exchange_body(**overrides: Any) -> dict[str, Any]:
    """All three fields the controller requires; pass ``field=None`` to drop one."""
    body: dict[str, Any] = {
        "code": "spec-audit-not-a-real-code",
        "provider": "oauth",
        "redirectUri": "http://localhost:3000/auth/oauth/callback",
        **overrides,
    }
    return {k: v for k, v in body.items() if v is not None}


def password_body(password: Any, email: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"method": "password", "credentials": {"password": password}, **extra}
    if email is not None:
        body["email"] = email
    return body


def otp_body(code: Any, email: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"method": "otp", "credentials": {"otp": code}}
    if email is not None:
        body["email"] = email
    return body


class UserAccountAuditClient(APIClient):
    """Client for /api/v1/userAccount.

    No route takes the suite's OAuth token, so nothing is sent unless asked for:
    ``token=`` sends that bearer token and ``session=`` the initAuth session token.
    """

    BASE = USER_ACCOUNT_BASE

    def _call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        token: str | None = None,
        auth: bool = False,
        **kwargs: Any,
    ) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        if token is not None:
            # auth=False so the helper neither overwrites the header nor
            # retries a 401 with a refreshed admin token.
            auth = False
            kwargs["headers"] = {**bearer(token), **(kwargs.get("headers") or {})}
        return self._client.request(method, self._path(path), auth=auth, **kwargs)

    def init_auth(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/initAuth", body, **kwargs)

    def authenticate(
        self, body: Any = None, *, session: str | None = None, **kwargs: Any
    ) -> requests.Response:
        if session is not None:
            kwargs["headers"] = {"x-session-token": session, **(kwargs.get("headers") or {})}
        return self._call("POST", "/authenticate", body, **kwargs)

    def otp_generate(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/login/otp/generate", body, **kwargs)

    def password_forgot(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/password/forgot", body, **kwargs)

    def password_reset(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/password/reset", body, **kwargs)

    def password_reset_token(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/password/reset/token", body, **kwargs)

    def refresh_token(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/refresh/token", body, **kwargs)

    def logout(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/logout/manual", body, **kwargs)

    def oauth_exchange(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("POST", "/oauth/exchange", body, **kwargs)

    def validate_email_change(self, body: Any = None, **kwargs: Any) -> requests.Response:
        return self._call("PUT", "/validateEmailChange", body, **kwargs)

    def start_session(self, email: str | None = None) -> str:
        """A fresh sign-in session token; ``email`` is stored on the session when given."""
        resp = self.init_auth(None if email is None else {"email": email})
        assert resp.status_code == 200, resp.text[:500]
        token = resp.headers.get("x-session-token")
        assert token, "initAuth returned no x-session-token header"
        return token

    def sign_in(self, account: Account, password: str | None = None) -> Tokens:
        resp = self.authenticate(
            password_body(password or account.password, account.email),
            session=self.start_session(),
        )
        assert resp.status_code == 200, resp.text[:500]
        body = resp.json()
        return Tokens(access=body["accessToken"], refresh=body["refreshToken"])


@contextmanager
def _database() -> Iterator[Any]:
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        yield client[MONGO_DB_NAME]
    finally:
        client.close()


def _id_forms(value: str) -> list[Any]:
    try:
        return [value, ObjectId(value)]
    except (InvalidId, TypeError):
        return [value]


def seed_password(org_id: str, user_id: str, password: str = ACCOUNT_PASSWORD) -> None:
    """Give an account a password; POST /users creates it with none and the invite needs a person."""
    now = datetime.datetime.now(datetime.timezone.utc)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with _database() as db:
        db.userCredentials.delete_many({"userId": user_id, "orgId": org_id})
        db.userCredentials.insert_one(
            {
                "userId": user_id,
                "orgId": org_id,
                "hashedPassword": hashed,
                "ipAddress": "127.0.0.1",
                "wrongCredentialCount": 0,
                "isBlocked": False,
                "forceNewPasswordGeneration": False,
                "isDeleted": False,
                "createdAt": now,
                "updatedAt": now,
            }
        )


def lock_account(org_id: str, user_id: str, *, hours: int = 24) -> None:
    """Mark the account locked the way five wrong passwords would."""
    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)
    with _database() as db:
        db.userCredentials.update_many(
            {"userId": user_id, "orgId": org_id},
            {"$set": {"isBlocked": True, "blockExpiresAt": until, "wrongCredentialCount": 5}},
        )


def credentials_rows(org_id: str, user_id: str) -> int:
    with _database() as db:
        return db.userCredentials.count_documents({"userId": user_id, "orgId": org_id})


def delete_credentials(org_id: str, user_id: str) -> None:
    with _database() as db:
        db.userCredentials.delete_many({"userId": user_id, "orgId": org_id})


def forget_account(user_id: str, *emails: str) -> None:
    """Remove the rows sign-in routes write for an account and that no API deletes."""
    ids = _id_forms(user_id)
    with _database() as db:
        db.userCredentials.delete_many({"userId": {"$in": ids}})
        db.usedPasswordResetLinks.delete_many({"userId": {"$in": ids}})
        db.userActivities.delete_many({"userId": {"$in": ids}})
        if emails:
            db.userActivities.delete_many({"email": {"$in": list(emails)}})


def forget_mail(*addresses: str) -> None:
    """Remove from Mailpit every message sent to one of ``addresses``."""
    ids: set[str] = set()
    for address in addresses:
        ids |= mailpit.message_ids(address)
    if ids:
        resp = requests.delete(
            f"{mailpit.mailpit_url()}/api/v1/messages", json={"IDs": sorted(ids)}, timeout=10
        )
        assert resp.status_code == 200, f"Mailpit delete answered {resp.status_code}"


SIGN_IN_CODE_SUBJECT = "OTP for Login"
RESET_LINK_SUBJECT = "Reset your password"


def emailed_sign_in_code(client: UserAccountAuditClient, email: str) -> str:
    """Ask for a sign-in code for ``email`` and read it from the mail Mailpit caught."""
    seen = mailpit.message_ids(email)
    resp = client.otp_generate({"email": email})
    assert resp.status_code == 200, resp.text[:500]
    return mailpit.sign_in_code(mailpit.wait_for_new_message(email, SIGN_IN_CODE_SUBJECT, seen))


def emailed_reset_token(client: UserAccountAuditClient, email: str) -> str:
    """Ask for a reset link for ``email`` and read its token from the mail Mailpit caught."""
    seen = mailpit.message_ids(email)
    resp = client.password_forgot({"email": email})
    assert resp.status_code == 200, resp.text[:500]
    return mailpit.reset_link_token(mailpit.wait_for_new_message(email, RESET_LINK_SUBJECT, seen))


def forget_email_activity(*emails: str) -> None:
    """Remove the activity rows sign-in routes write for an address that has no account."""
    with _database() as db:
        db.userActivities.delete_many({"email": {"$in": list(emails)}})


def create_member(client: PipeshubClient, prefix: str = "spec-audit-member") -> DisposableMember:
    email = unused_email(prefix)
    resp = client.request(
        "POST",
        "/api/v1/users",
        json={"email": email, "fullName": f"Spec Audit {uuid.uuid4().hex[:8]}", "role": "member"},
    )
    assert resp.status_code in (200, 201), f"createUser failed: {resp.status_code} {resp.text[:300]}"
    user = resp.json()
    user_id = str(user.get("_id") or user.get("id") or "")
    assert user_id, f"createUser response has no id: {sorted(user)}"
    return DisposableMember(
        user_id=user_id, org_id=str(user.get("orgId") or client.org_id), email=email
    )


def create_account(client: PipeshubClient, prefix: str = "spec-audit-account") -> Account:
    member = create_member(client, prefix)
    seed_password(member.org_id, member.user_id)
    return Account(user_id=member.user_id, org_id=member.org_id, email=member.email)


def delete_member(client: PipeshubClient, member: DisposableMember, *emails: str) -> None:
    resp = client.request("DELETE", f"/api/v1/users/{member.user_id}")
    forget_account(member.user_id, member.email, *emails)
    assert resp.status_code in (200, 404), f"deleteUser failed: {resp.status_code} {resp.text[:300]}"


class SignInPolicy:
    """The org's sign-in policy, read and written as an admin signed in with a password."""

    def __init__(self, client: PipeshubClient, admin_headers: dict[str, str]) -> None:
        self._client = client
        self._headers = admin_headers

    def current(self) -> list[dict[str, Any]]:
        resp = self._client.request("GET", AUTH_METHODS_ROUTE, auth=False, headers=self._headers)
        assert resp.status_code == 200, resp.text[:500]
        return list(resp.json()["authMethods"])

    def replace(self, steps: list[dict[str, Any]]) -> requests.Response:
        resp = self._client.request(
            "POST",
            UPDATE_AUTH_METHOD_ROUTE,
            auth=False,
            headers=self._headers,
            json={"authMethod": steps},
        )
        assert resp.status_code == 200, resp.text[:500]
        return resp

    @contextmanager
    def temporarily(self, steps: list[dict[str, Any]]) -> Iterator[None]:
        """Other suites sign in against this policy, so it is changed for one block and put back."""
        original = self.current()
        self.replace(steps)
        try:
            yield
        finally:
            self.replace(original)

    def session_under(
        self, steps: list[dict[str, Any]], account_client: UserAccountAuditClient, email: str | None = None
    ) -> str:
        """A sign-in session that carries ``steps``; the org's policy is back before this returns."""
        with self.temporarily(steps):
            return account_client.start_session(email)


def steps(*methods_per_step: list[str]) -> list[dict[str, Any]]:
    return [
        {"order": order, "allowedMethods": [{"type": method} for method in methods]}
        for order, methods in enumerate(methods_per_step, start=1)
    ]


def _settings_store() -> redis.Redis:
    assert os.getenv("KV_STORE_TYPE", "redis").strip().lower() == "redis", (
        "sign-in provider settings have no delete route; restoring them needs the Redis "
        "configuration store (KV_STORE_TYPE=redis)"
    )
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("REDIS_DB", "0")),
    )



_SECRET_KEYS_SETTING = "/services/secretKeys"


def stored_signing_secret(field: str) -> str:
    """A signing secret Node keeps encrypted in its configuration store (ConfigService.getOrCreateSecret).

    Access tokens are signed with ``jwtSecret``, which the test environment does not carry.
    The store encrypts with AES-256-GCM under sha256(SECRET_KEY), as ``iv:ciphertext:tag`` in hex.
    """
    secret_key = os.getenv("SECRET_KEY", "")
    assert secret_key, "SECRET_KEY is not set in the test environment; the stored signing secrets cannot be read"
    store = _settings_store()
    try:
        raw = store.get(f"{_KV_PREFIX}{_SECRET_KEYS_SETTING}")
    finally:
        store.close()
    assert raw, "the configuration store holds no signing secrets"
    value = raw.decode()
    try:
        decoded = json.loads(value)
        value = decoded if isinstance(decoded, str) else value
    except ValueError:
        pass
    iv, ciphertext, tag = (bytes.fromhex(part) for part in value.split(":"))
    key = hashlib.sha256(secret_key.encode()).digest()
    keys = json.loads(AESGCM(key).decrypt(iv, ciphertext + tag, None))
    return str(keys[field])


@contextmanager
def preserved_setting(key: str) -> Iterator[None]:
    """Put a configuration-store entry back exactly as it was (or absent) when the block ends.

    The sign-in provider settings can be written through the API but never cleared.
    """
    store = _settings_store()
    name = f"{_KV_PREFIX}{key}"
    try:
        before = store.get(name)
        yield
    finally:
        if before is None:
            store.delete(name)
        else:
            store.set(name, before)
        store.publish(_KV_INVALIDATION_CHANNEL, key)
        store.close()


@contextmanager
def without_setting(key: str) -> Iterator[None]:
    """Run the block with a configuration-store entry absent, then put it back."""
    with preserved_setting(key):
        store = _settings_store()
        try:
            store.delete(f"{_KV_PREFIX}{key}")
            store.publish(_KV_INVALIDATION_CHANNEL, key)
        finally:
            store.close()
        yield


StubAnswer = tuple[int, Any]


class OAuthProviderStub:
    """An OAuth 2.0 provider on localhost: a token endpoint and a userinfo endpoint the API calls.

    ``codes`` maps an authorization code to the token endpoint's answer and ``access_tokens``
    a bearer token to the userinfo answer, each as ``(status, body)``; a ``str`` body is sent as
    it is, anything else as JSON.
    """

    def __init__(self) -> None:
        self.codes: dict[str, StubAnswer] = {}
        self.access_tokens: dict[str, StubAnswer] = {}
        self.token_requests: list[dict[str, str]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _answer(self, answer: StubAnswer) -> None:
                status, body = answer
                raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header(
                    "Content-Type", "text/plain" if isinstance(body, str) else "application/json"
                )
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                stub.token_requests.append(form)
                self._answer(
                    stub.codes.get(form.get("code", ""), (400, {"error": "invalid_grant"}))
                )

            def do_GET(self) -> None:  # noqa: N802
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                self._answer(stub.access_tokens.get(token, (401, {"error": "invalid_token"})))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self.token_endpoint = f"{self.base_url}/token"
        self.userinfo_endpoint = f"{self.base_url}/userinfo"

    def issue(self, email: str | None, **token_fields: Any) -> tuple[str, str]:
        """A fresh ``(code, access_token)`` pair; userinfo answers with ``email`` when given."""
        code, access_token = f"code-{uuid.uuid4().hex}", f"at-{uuid.uuid4().hex}"
        self.codes[code] = (
            200,
            {
                "access_token": access_token,
                "id_token": f"id-{uuid.uuid4().hex}",
                "token_type": "Bearer",
                "expires_in": 3600,
                **token_fields,
            },
        )
        self.access_tokens[access_token] = (200, {} if email is None else {"email": email})
        return code, access_token

    def config(self, *, enable_jit: bool = False, **overrides: Any) -> dict[str, Any]:
        """A body for POST /configurationManager/authConfig/oauth that points at this stub."""
        return {
            "providerName": "spec-audit-oauth",
            "clientId": "spec-audit-client",
            "clientSecret": "spec-audit-secret",
            "authorizationUrl": f"{self.base_url}/authorize",
            "tokenEndpoint": self.token_endpoint,
            "userInfoEndpoint": self.userinfo_endpoint,
            "scope": "openid email",
            "redirectUri": "http://localhost:3000/auth/oauth/callback",
            "enableJit": enable_jit,
            **overrides,
        }

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def configure_oauth(client: PipeshubClient, body: dict[str, Any]) -> None:
    resp = client.request("POST", OAUTH_CONFIG_PATH, json=body)
    assert resp.status_code == 200, f"saving the OAuth sign-in settings failed: {resp.status_code} {resp.text[:300]}"
