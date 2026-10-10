"""Strict OpenAPI audit of POST /api/v1/skills/import/npm/preview.

The success cases download a small pinned package from the public npm registry. The import
routes share a 10 calls/minute limiter per user, so the calls run as a disposable member.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    NPM_MULTI_SKILL_PACKAGE,
    NPM_MULTI_SKILL_PICK,
    SkillsClient,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/npm/preview"
PATH = "/import/npm/preview"

PICK_COMMAND = f"npx skills add {NPM_MULTI_SKILL_PACKAGE} --skill {NPM_MULTI_SKILL_PICK}"
# Refused by the Python command parser before any registry lookup.
SHELL_INJECTION_COMMAND = "npm install left-pad; rm -rf /"
UNKNOWN_FLAG_COMMAND = "npm install --registry"
MISSING_PACKAGE = "spec-audit-no-such-package-zz9@1.0.0"


def test_npm_preview_returns_the_picked_skill(import_user: SecondUser) -> None:
    resp = request_as(import_user, "POST", PATH, json={"command_or_name": PICK_COMMAND})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    preview = resp.json()
    assert preview["name"] == NPM_MULTI_SKILL_PICK
    assert preview["content"].startswith("---\n")
    assert preview["sourceLabel"] == f"npm:{NPM_MULTI_SKILL_PACKAGE}"


def test_npm_preview_ignores_unknown_body_field(import_user: SecondUser) -> None:
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = request_as(
            import_user, "POST", PATH,
            json={"command_or_name": PICK_COMMAND, "spec_audit_unknown": True},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["name"] == NPM_MULTI_SKILL_PICK


def test_npm_preview_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, auth=False, json={"command_or_name": "left-pad"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-command"),
        pytest.param({"command_or_name": ""}, id="empty-command"),
        pytest.param({"command_or_name": 5}, id="command-not-text"),
    ],
)
def test_npm_preview_rejects_invalid_body(import_user: SecondUser, body: dict[str, Any]) -> None:
    with outside_request_contract("a body the spec does not allow"):
        resp = request_as(import_user, "POST", PATH, json=body)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == ["body", "command_or_name"]
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("command", "detail_start"),
    [
        pytest.param(SHELL_INJECTION_COMMAND, None, id="shell-metacharacters"),
        pytest.param(UNKNOWN_FLAG_COMMAND, None, id="unknown-flag"),
        pytest.param(MISSING_PACKAGE, "Package 'spec-audit-no-such-package-zz9@1.0.0' was not found", id="not-on-registry"),
    ],
)
def test_npm_preview_refuses_command(
    import_user: SecondUser, command: str, detail_start: str | None
) -> None:
    # No admin gate: a member reaches the parser and the registry lookup.
    resp = request_as(import_user, "POST", PATH, json={"command_or_name": command})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    if detail_start is not None:
        assert detail.startswith(detail_start), detail
