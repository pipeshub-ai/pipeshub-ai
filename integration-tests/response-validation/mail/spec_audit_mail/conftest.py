"""Shared fixtures for the strict OpenAPI audit of /api/v1/mail."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest
import requests
from pymongo import MongoClient
from pymongo.collection import Collection

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper import mailpit  # noqa: E402
from helper.config import MONGO_DB_NAME, MONGO_URI  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from mail_audit_support import (  # noqa: E402
    AUDIT_COLLECTION,
    OTHER_SCOPE,
    MailClient,
    MintScopedToken,
    NewRecipient,
    forget,
    mint_scoped_token,
    new_address,
    scoped_jwt_secret,
    smtp_is_configured,
)


@pytest.fixture(scope="session")
def mail_client(pipeshub_client: PipeshubClient) -> MailClient:
    return MailClient(pipeshub_client)


@pytest.fixture(scope="session")
def scoped_token(
    pipeshub_client: PipeshubClient, mail_client: MailClient
) -> MintScopedToken:
    """Factory: ``scoped_token(SEND_MAIL_SCOPE)`` -> a service token the deployment accepts.

    Extra positional scopes and keyword claims are passed through; ``userId`` and
    ``orgId`` default to the shared admin's.
    """
    secret = scoped_jwt_secret()
    if not secret:
        pytest.fail(
            "SCOPED_JWT_SECRET is not set: mail routes accept only scoped service tokens, "
            "and no API issues one for these scopes"
        )

    def _mint(*scopes: str, ttl_seconds: int = 3600, **claims: Any) -> str:
        claims.setdefault("userId", pipeshub_client.acting_user_id)
        claims.setdefault("orgId", pipeshub_client.org_id)
        return mint_scoped_token(secret, list(scopes), ttl_seconds=ttl_seconds, **claims)

    # A wrong scope is refused either way, but a good signature gets "Invalid scope"
    # and a bad one "Invalid token" (AuthTokenService.verifyScopedToken).
    probe = mail_client.update_smtp_config(token=_mint(OTHER_SCOPE))
    assert probe.status_code == 401, probe.text[:500]
    if probe.json()["error"]["message"] != "Invalid scope":
        pytest.fail(
            "SCOPED_JWT_SECRET is not the scoped JWT secret this deployment verifies "
            "with (it answers 'Invalid token' to a token signed with it)"
        )

    return _mint


@pytest.fixture(scope="session")
def smtp_ready(pipeshub_client: PipeshubClient) -> None:
    """The stack must have SMTP configured and Mailpit readable, or no send can be judged."""
    if not smtp_is_configured(pipeshub_client):
        pytest.fail(
            "SMTP is not configured on this stack: sendEmail answers 400 to every "
            "request. Save an SMTP configuration that points at Mailpit."
        )
    try:
        resp = requests.get(f"{mailpit.mailpit_url()}/api/v1/info", timeout=10)
    except requests.RequestException as exc:
        pytest.fail(f"Mailpit is not reachable at {mailpit.mailpit_url()}: {exc}")
    assert resp.status_code == 200, f"Mailpit info answered {resp.status_code}"


@pytest.fixture(scope="session")
def mail_audit_collection() -> Iterator[Collection]:
    """Where the mail service records each send; no API removes those records."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        yield client[MONGO_DB_NAME][AUDIT_COLLECTION]
    finally:
        client.close()


@pytest.fixture
def new_recipient(mail_audit_collection: Collection) -> Iterator[NewRecipient]:
    """Factory for fresh addresses; the mail and audit records they caused are removed afterwards."""
    used: list[str] = []

    def _new() -> str:
        used.append(new_address())
        return used[-1]

    yield _new
    forget(used)
    if used:
        mail_audit_collection.delete_many(
            {"$or": [{"to": {"$in": used}}, {"cc": {"$in": used}}]}
        )
