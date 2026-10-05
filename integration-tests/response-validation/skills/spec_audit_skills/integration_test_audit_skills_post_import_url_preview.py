"""Strict OpenAPI audit of POST /api/v1/skills/import/url/preview.

No success case: a real preview downloads an archive from the internet. The route sits
behind a 10 requests/minute per-user limiter counted before validation, so the admin
makes two calls here and the member one.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import SkillsClient, request_as
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/url/preview"
PATH = "/import/url/preview"

NON_HTTP_URL = "ftp://example.com/spec-audit-skill.zip"
# A loopback literal is refused by the SSRF guard without any outbound request.
LOOPBACK_URL = "http://127.0.0.1/spec-audit-skill.zip"


def _skip_if_skills_disabled(status_code: int, text: str) -> None:
    if status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {text[:200]}")


def test_preview_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, auth=False, json={"url": NON_HTTP_URL})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "expected_status", "detail"),
    [
        pytest.param({}, 422, None, id="missing-url"),
        pytest.param(
            {"url": NON_HTTP_URL}, 400, "Only http(s) URLs are supported.", id="non-http-scheme"
        ),
    ],
)
def test_preview_rejects_bad_body(
    skills_client: SkillsClient,
    body: dict[str, Any],
    expected_status: int,
    detail: str | None,
) -> None:
    resp = skills_client.post(PATH, json=body)
    _skip_if_skills_disabled(resp.status_code, resp.text)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    if detail is not None:
        assert resp.json() == {"detail": detail}


def test_member_preview_of_private_address_is_refused(second_user: SecondUser) -> None:
    # No admin gate: the member reaches the importer and gets its 400, not a 403.
    resp = request_as(second_user, "POST", PATH, json={"url": LOOPBACK_URL})
    _skip_if_skills_disabled(resp.status_code, resp.text)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "detail": "This URL is not allowed: it must point to a public address."
    }
