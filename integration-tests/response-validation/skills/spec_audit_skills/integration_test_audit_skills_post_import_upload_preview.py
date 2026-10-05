"""Strict OpenAPI audit of POST /api/v1/skills/import/upload/preview."""

from __future__ import annotations

import io
import zipfile

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    RESOURCE_PATH,
    SkillsClient,
    request_as,
    skill_md,
    unique_skill_name,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/upload/preview"
PATH = "/import/upload/preview"
ARCHIVE_NAME = "spec-audit-skill.zip"
RESOURCE_TEXT = "Bundled by the skills spec audit.\n"

# The import routes share a 10 requests/minute per-user limiter, so the calls
# in this file are split between the admin and the member.


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in members.items():
            archive.writestr(path, text)
    return buffer.getvalue()


def _upload(data: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (ARCHIVE_NAME, data, "application/zip")}


def test_upload_preview_returns_parsed_skill(skills_client: SkillsClient) -> None:
    name = unique_skill_name()
    content = skill_md(name)
    archive = _zip({"SKILL.md": content, RESOURCE_PATH: RESOURCE_TEXT})

    resp = skills_client.post(PATH, files=_upload(archive))

    # Preview is stateless: nothing is persisted, so there is nothing to clean up.
    assert resp.status_code == 200, resp.text[:500]
    preview = resp.json()
    assert preview["name"] == name
    assert preview["content"] == content
    assert preview["resources"] == {RESOURCE_PATH: RESOURCE_TEXT}
    assert preview["skippedBinaryResources"] == []
    assert preview["sourceLabel"] == f"upload:{ARCHIVE_NAME}"
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_preview_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, auth=False, files=_upload(_zip({"SKILL.md": "x"})))

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_preview_without_file_is_rejected_by_node(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", PATH)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == {"detail": "A file upload is required (field 'file')"}
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_preview_rejects_non_archive(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, files=_upload(b"plain text, not an archive"))

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == {"detail": "Not a valid zip or tar/tgz archive."}
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_preview_rejects_archive_without_skill_md(second_user: SecondUser) -> None:
    archive = _zip({"README.md": "# Not a skill\n"})

    resp = request_as(second_user, "POST", PATH, files=_upload(archive))

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["detail"].startswith("No SKILL.md found in the archive.")
    assert_strict_openapi_response(resp, ROUTE)
