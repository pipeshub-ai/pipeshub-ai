"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/mail."""

from __future__ import annotations

import datetime
import os
from typing import Any, Callable

import jwt
import requests

from helper.http.api_client import APIClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

MAIL_BASE = "/api/v1/mail"
SEND_EMAIL_ROUTE = f"{MAIL_BASE}/emails/sendEmail"
UPDATE_SMTP_CONFIG_ROUTE = f"{MAIL_BASE}/updateSmtpConfig"

SEND_MAIL_SCOPE = "mail:send"
FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope neither mail route accepts.
OTHER_SCOPE = "storage:token"

# Rendering fails before any SMTP connection is opened, so no mail leaves.
UNKNOWN_TEMPLATE_TYPE = "specAuditUnknownTemplate"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

SMTP_STATUS_PATH = "/api/v1/configurationManager/smtpConfig/status"

MintScopedToken = Callable[..., str]


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    secret: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 3600,
    **claims: Any,
) -> str:
    """Service token shaped like Node's createJwt ones; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, secret, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class MailClient(APIClient):
    """Client for /api/v1/mail.

    Both routes take a scoped service token, never a session token: pass
    ``token=`` to send one. Without it, ``auth=True`` sends the shared admin's
    session token (which the routes reject) and ``auth=False`` sends nothing.
    """

    BASE = MAIL_BASE

    def _post(
        self, path: str, token: str | None, auth: bool, **kwargs: Any
    ) -> requests.Response:
        if token is not None:
            # auth=False so the helper neither overwrites the header nor
            # retries a 401 with a refreshed admin token.
            return self.post(path, auth=False, headers=bearer(token), **kwargs)
        return self.post(path, auth=auth, **kwargs)

    def send_email(
        self,
        body: Any = None,
        *,
        token: str | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        return self._post("/emails/sendEmail", token, auth, **kwargs)

    def update_smtp_config(
        self,
        body: Any = None,
        *,
        token: str | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        return self._post("/updateSmtpConfig", token, auth, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a mail route with the non-admin member's session token; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method, f"{user.base_url}{MAIL_BASE}{path}", headers=user.headers, **kwargs
    )


def smtp_is_configured(client: PipeshubClient) -> bool:
    """Whether the stored SMTP config passes the same host/port/fromEmail gate as sendEmail.

    sendEmail checks SMTP *before* the token, so this decides whether a bad
    token answers 400 (not configured) or 401.
    """
    resp = client.request("GET", SMTP_STATUS_PATH)
    assert resp.status_code == 200, resp.text[:500]
    return bool(resp.json().get("configured"))


def unknown_template_body(org_id: str | None = None) -> dict[str, Any]:
    """A sendEmail body that can never deliver: the template type does not exist."""
    body: dict[str, Any] = {
        "emailTemplateType": UNKNOWN_TEMPLATE_TYPE,
        "sendEmailTo": ["spec-audit@example.invalid"],
        "subject": "spec audit",
        "templateData": {},
    }
    if org_id:
        body["orgId"] = org_id
    return body
