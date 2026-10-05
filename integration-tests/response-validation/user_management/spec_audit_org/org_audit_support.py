"""Constants and helpers for the strict OpenAPI audit of /api/v1/org."""

from __future__ import annotations

from typing import Any

import requests

from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser

ORG_BASE = "/api/v1/org"
ORG_ROUTE = ORG_BASE

INVALID_BEARER = {"Authorization": "Bearer not-a-jwt"}

ORG_EXISTS_MESSAGE = "There is already an organization"
ADMIN_SCOPE = "org:admin"

# Satisfies passwordValidator: upper, lower, digit, one of #?!@$%^&*-, 8..72 bytes.
VALID_PASSWORD = "SpecAudit123!"

OrgBody = dict[str, Any]


def org_creation_body(account_type: str = "individual", **overrides: Any) -> OrgBody:
    """A body that passes OrgCreationBody; pass ``field=None`` to drop a field."""
    body: OrgBody = {
        "accountType": account_type,
        "contactEmail": "spec-audit-admin@example.com",
        "adminFullName": "Spec Audit Admin",
        "password": VALID_PASSWORD,
    }
    if account_type == "business":
        body["registeredName"] = "Spec Audit Corp"
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


def current_org_id(org_client: OrgClient) -> str:
    """The live org's id, read through GET /org, which hides a soft-deleted org."""
    resp = org_client.get_organization()
    assert resp.status_code == 200, f"shared org is not readable: {resp.status_code} {resp.text[:300]}"
    return str(resp.json()["_id"])


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an org route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{ORG_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
