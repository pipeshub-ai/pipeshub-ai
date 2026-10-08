"""Strict OpenAPI audit of POST /api/v1/skills/import/url/preview.

The success cases download a small pinned public archive (an npm tarball). The import
routes share a 10 calls/minute limiter per user, so the calls run as a disposable member.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import SINGLE_SKILL_TARBALL_URL, SkillsClient, request_as
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/url/preview"
PATH = "/import/url/preview"

NON_HTTP_URL = "ftp://example.com/spec-audit-skill.zip"
# A loopback literal is refused by the SSRF guard without any outbound request.
LOOPBACK_URL = "http://127.0.0.1/spec-audit-skill.zip"


def test_preview_of_public_archive(import_user: SecondUser) -> None:
    resp = request_as(import_user, "POST", PATH, json={"url": SINGLE_SKILL_TARBALL_URL})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    preview = resp.json()
    assert preview["name"] == "ai-skills"
    assert preview["sourceLabel"] == f"url:{SINGLE_SKILL_TARBALL_URL}"


def test_preview_ignores_unknown_body_field(import_user: SecondUser) -> None:
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = request_as(
            import_user, "POST", PATH, json={"url": SINGLE_SKILL_TARBALL_URL, "spec_audit_unknown": 1}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_preview_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, auth=False, json={"url": NON_HTTP_URL})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [pytest.param({}, id="missing-url"), pytest.param({"url": ""}, id="empty-url"), pytest.param({"url": 5}, id="url-not-text")],
)
def test_preview_rejects_invalid_body(import_user: SecondUser, body: dict[str, Any]) -> None:
    with outside_request_contract("a body the spec does not allow"):
        resp = request_as(import_user, "POST", PATH, json=body)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == ["body", "url"]
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        pytest.param(NON_HTTP_URL, "Only http(s) URLs are supported.", id="non-http-scheme"),
        pytest.param(LOOPBACK_URL, "This URL is not allowed: it must point to a public address.", id="private-address"),
    ],
)
def test_preview_refuses_url(import_user: SecondUser, url: str, detail: str) -> None:
    # No admin gate: the member reaches the importer and gets its 400, not a 403.
    resp = request_as(import_user, "POST", PATH, json={"url": url})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": detail}
