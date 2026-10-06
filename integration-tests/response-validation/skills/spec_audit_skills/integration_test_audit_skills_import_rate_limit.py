"""The four skills import routes share one limiter of 10 calls per minute per user.

The limiter is keyed by the caller's user id, so the module exhausts the budget of its own
disposable member and throttles nobody else. It counts refused calls too.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import IMPORT_CALLS_PER_MINUTE, request_as, skill_md, unique_skill_name
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

UPLOAD = "/import/upload/preview"
IMPORT_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("/import/npm/preview", "/api/v1/skills/import/npm/preview", {"json": {"command_or_name": "left-pad"}}),
    ("/import/url/preview", "/api/v1/skills/import/url/preview", {"json": {"url": "https://example.com/a.zip"}}),
    (UPLOAD, "/api/v1/skills/import/upload/preview", {"files": {"file": ("a.zip", b"x", "application/zip")}}),
    ("/import/finalize", "/api/v1/skills/import/finalize", {"json": {"content": skill_md(unique_skill_name())}}),
]


@pytest.fixture(scope="module")
def throttled_user(import_user: SecondUser) -> SecondUser:
    for _ in range(IMPORT_CALLS_PER_MINUTE):
        # Refused by the gateway itself (no file), so nothing reaches the skills service.
        resp = request_as(import_user, "POST", UPLOAD)
        assert resp.status_code == 400, resp.text[:500]
    return import_user


@pytest.mark.parametrize(("path", "route", "kwargs"), IMPORT_CALLS, ids=[c[0] for c in IMPORT_CALLS])
def test_import_call_over_the_limit_is_throttled(
    throttled_user: SecondUser, path: str, route: str, kwargs: dict[str, Any]
) -> None:
    resp = request_as(throttled_user, "POST", path, **kwargs)
    assert resp.status_code == 429, resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_TOO_MANY_REQUESTS"
    assert error["message"] == "Too many skill import requests. Please try again later."
    assert 0 < error["retryAfter"] <= 60
    assert resp.headers["RateLimit-Limit"] == str(IMPORT_CALLS_PER_MINUTE)
