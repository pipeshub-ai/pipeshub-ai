"""Every /api/v1/notifications route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check or the validator.
"""

from __future__ import annotations

import pytest
from notifications_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_NOTIFICATION_ID,
    NOTIFICATIONS_BASE,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "", ""),
        ("GET", "/stats", "/stats"),
        ("PATCH", "/read-all", "/read-all"),
        ("PATCH", f"/{MISSING_NOTIFICATION_ID}/read", "/:id/read"),
        ("PATCH", f"/{MISSING_NOTIFICATION_ID}/unread", "/:id/unread"),
        ("PATCH", f"/{MISSING_NOTIFICATION_ID}/archive", "/:id/archive"),
        ("PATCH", f"/{MISSING_NOTIFICATION_ID}/unarchive", "/:id/unarchive"),
        ("DELETE", f"/{MISSING_NOTIFICATION_ID}", "/:id"),
    ],
    ids=["list", "stats", "read_all", "read", "unread", "archive", "unarchive", "delete"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    pipeshub_client: PipeshubClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = pipeshub_client.request(
        method,
        f"{NOTIFICATIONS_BASE}{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{NOTIFICATIONS_BASE}{route}")
