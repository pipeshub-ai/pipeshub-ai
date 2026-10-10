"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/mail."""

from __future__ import annotations

import datetime
import os
import time
import uuid
from typing import Any, Callable

import jwt
import requests

from helper import mailpit
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

UNKNOWN_TEMPLATE_TYPE = "specAuditUnknownTemplate"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

SMTP_STATUS_PATH = "/api/v1/configurationManager/smtpConfig/status"
# The Mongo collection of MailModel (mailInfo.schema.ts).
AUDIT_COLLECTION = "mailInfo"

SENT_BODY = {"data": {"status": True, "data": "Email sent"}}
SEND_FAILED_CODE = "HTTP_INTERNAL_SERVER_ERROR"
SEND_FAILED_MESSAGE = "Something went wrong while PipesHub tried to send that email."

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

_LINK = "https://example.invalid/spec-audit/link"
# Template type -> (templateData, the key whose value must show up in the delivered HTML).
TEMPLATES: dict[str, tuple[dict[str, Any], str]] = {
    "loginWithOTP": ({"name": "Spec Audit", "otp": "482913"}, "otp"),
    "resetPassword": ({"name": "Spec Audit", "link": _LINK, "linkLifetime": "20 minutes"}, "link"),
    "resetEmail": ({"name": "Spec Audit", "link": _LINK}, "link"),
    "emailChangeNotice": (
        {"name": "Spec Audit", "orgName": "SpecAuditOrg", "newEmail": "new@example.invalid"},
        "newEmail",
    ),
    "accountCreation": ({"orgName": "SpecAuditOrg", "link": _LINK}, "orgName"),
    "orgEmailVerification": (
        {"name": "Spec Audit", "orgName": "SpecAuditOrg", "verificationLink": _LINK},
        "orgName",
    ),
    "appuserInvite": ({"invitee": "Spec Audit", "orgName": "SpecAuditOrg", "link": _LINK}, "link"),
    "suspiciousLoginAttempt": ({"name": "SpecAuditName"}, "name"),
    "domainLimitReached": (
        {"domain": "example.invalid", "limit": 5, "userEmail": "blocked@example.invalid"},
        "userEmail",
    ),
    "joinRequestNotify": (
        {"orgName": "SpecAuditOrg", "requesterEmail": "asker@example.invalid", "link": _LINK},
        "requesterEmail",
    ),
    "joinRequestDecision": ({"orgName": "SpecAuditOrg", "approved": True, "link": _LINK}, "link"),
}

MintScopedToken = Callable[..., str]
NewRecipient = Callable[[], str]


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    secret: str,
    scopes: list[str] | str | None,
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
            headers = {**bearer(token), **(kwargs.pop("headers", None) or {})}
            return self.post(path, auth=False, headers=headers, **kwargs)
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
    """Whether the stored SMTP config passes the same host/port/fromEmail gate as sendEmail."""
    resp = client.request("GET", SMTP_STATUS_PATH)
    assert resp.status_code == 200, resp.text[:500]
    return bool(resp.json().get("configured"))


def new_address() -> str:
    """An address no other message has gone to, so Mailpit can be searched by it."""
    return f"spec-audit-{uuid.uuid4().hex[:12]}@example.invalid"


def email_body(
    template: str = "loginWithOTP", *, to: Any = None, **fields: Any
) -> dict[str, Any]:
    """A deliverable sendEmail body; ``fields`` override or add, ``to=None`` leaves the recipient out."""
    body: dict[str, Any] = {
        "emailTemplateType": template,
        "subject": f"spec audit {template}",
        "templateData": dict(TEMPLATES[template][0]),
    }
    if to is not None:
        body["sendEmailTo"] = to
    body.update(fields)
    return body


def _search(field: str, address: str) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{mailpit.mailpit_url()}/api/v1/search",
        params={"query": f'{field}:"{address}"'},
        timeout=10,
    )
    assert resp.status_code == 200, f"Mailpit search answered {resp.status_code}"
    return list(resp.json().get("messages") or [])


def delivered(
    address: str, *, field: str = "to", wait_seconds: float = 15.0
) -> list[dict[str, Any]]:
    """Full Mailpit messages whose To (or Cc) holds ``address``.

    Waits up to ``wait_seconds`` for the first one; pass 0 to read the mailbox as it is.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        summaries = _search(field, address)
        if summaries or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    messages = []
    for summary in summaries:
        full = requests.get(
            f"{mailpit.mailpit_url()}/api/v1/message/{summary['ID']}", timeout=10
        )
        assert full.status_code == 200, f"Mailpit message answered {full.status_code}"
        messages.append(full.json())
    return messages


def forget(sent_to: list[str]) -> None:
    """Remove from Mailpit every message sent or copied to one of ``sent_to``."""
    ids = sorted(
        {m["ID"] for address in sent_to for field in ("to", "cc") for m in _search(field, address)}
    )
    if ids:
        resp = requests.delete(
            f"{mailpit.mailpit_url()}/api/v1/messages", json={"IDs": ids}, timeout=10
        )
        assert resp.status_code == 200, f"Mailpit delete answered {resp.status_code}"


def addresses(entries: list[dict[str, Any]] | None) -> list[str]:
    return [entry["Address"] for entry in entries or []]
